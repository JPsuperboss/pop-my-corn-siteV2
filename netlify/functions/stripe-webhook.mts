import type { Context, Config } from "@netlify/functions";
import { getStore } from "@netlify/blobs";
import { products } from "./products-data.mts";

/**
 * Vérifie la signature d'un webhook Stripe sans dépendance au package npm "stripe"
 * (même contrainte que create-checkout.mts : registre npm inaccessible depuis
 * l'environnement de build utilisé pour écrire ce code — Web Crypto natif à la place).
 * Référence de l'algorithme : https://stripe.com/docs/webhooks/signatures
 */
async function verifyStripeSignature(
  rawBody: string,
  sigHeader: string | null,
  secret: string,
  toleranceSeconds = 300
): Promise<boolean> {
  if (!sigHeader) return false;

  const parts: { t?: string; v1: string[] } = { v1: [] };
  for (const pair of sigHeader.split(",")) {
    const [key, value] = pair.split("=");
    if (key === "t") parts.t = value;
    if (key === "v1" && value) parts.v1.push(value);
  }
  if (!parts.t || parts.v1.length === 0) return false;

  const timestamp = Number(parts.t);
  if (!Number.isFinite(timestamp)) return false;
  // Protection anti-rejeu : on refuse un événement trop vieux (ou daté dans le futur).
  if (Math.abs(Date.now() / 1000 - timestamp) > toleranceSeconds) return false;

  const signedPayload = `${parts.t}.${rawBody}`;
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sigBuffer = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(signedPayload));
  const expectedHex = Array.from(new Uint8Array(sigBuffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");

  // Comparaison à temps constant, et on accepte si l'une des signatures v1 correspond
  // (Stripe peut en envoyer plusieurs lors d'une rotation de secret).
  return parts.v1.some((candidate) => timingSafeEqualHex(candidate, expectedHex));
}

function timingSafeEqualHex(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/** Reconstruit des lignes de commande lisibles depuis le résumé compact stocké en metadata ("id:qty,id:qty"). */
function parseCartSummary(summary: string | undefined) {
  if (!summary) return [];
  return summary
    .split(",")
    .map((pair) => {
      const [id, qtyStr] = pair.split(":");
      if (!id) return null;
      const product = products.find((p) => p.id === id);
      const qty = Number(qtyStr) || 1;
      return {
        id,
        qty,
        name: product?.name ?? id,
        unitPrice: product?.price ?? null,
      };
    })
    .filter((line): line is NonNullable<typeof line> => line !== null);
}

async function sendOrderEmail(order: {
  sessionId: string;
  customerEmail: string | null;
  amountTotal: number;
  shippingAddress: string | null;
  lines: { name: string; qty: number }[];
}) {
  const apiKey = Netlify.env.get("RESEND_API_KEY");
  if (!apiKey) {
    console.warn("RESEND_API_KEY manquante : commande enregistrée mais aucun email envoyé.");
    return; // non bloquant : la commande reste sauvegardée dans Netlify Blobs
  }

  const to = Netlify.env.get("ORDER_NOTIFY_EMAIL") || "julie.le-gal@orange.fr";
  const from = Netlify.env.get("ORDER_NOTIFY_FROM") || "onboarding@resend.dev";
  const lines = order.lines.map((l) => `- ${l.name} x${l.qty}`).join("<br>");

  try {
    const res = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        from,
        to,
        subject: `Nouvelle commande Pop My Corn — ${order.sessionId.slice(-8)}`,
        html: `
          <h2>Nouvelle commande</h2>
          <p><strong>Client·e :</strong> ${order.customerEmail ?? "non renseigné"}</p>
          <p><strong>Montant :</strong> ${(order.amountTotal / 100).toFixed(2)} €</p>
          <p><strong>Articles :</strong><br>${lines}</p>
          <p><strong>Adresse de livraison :</strong><br>${order.shippingAddress ?? "non renseignée"}</p>
        `,
      }),
    });
    if (!res.ok) {
      console.error("Échec envoi email Resend :", await res.text());
    }
  } catch (err) {
    console.error("Erreur réseau vers Resend :", err);
  }
}

export default async (req: Request, context: Context) => {
  if (req.method !== "POST") {
    return new Response("Méthode non autorisée.", { status: 405 });
  }

  const webhookSecret = Netlify.env.get("STRIPE_WEBHOOK_SECRET");
  if (!webhookSecret) {
    console.error("STRIPE_WEBHOOK_SECRET manquant.");
    return new Response("Configuration manquante.", { status: 500 });
  }

  // Important : on lit le corps brut AVANT tout parsing JSON — la signature
  // porte sur les octets exacts envoyés par Stripe.
  const rawBody = await req.text();
  const signature = req.headers.get("stripe-signature");

  const valid = await verifyStripeSignature(rawBody, signature, webhookSecret);
  if (!valid) {
    return new Response("Signature invalide.", { status: 400 });
  }

  let event: any;
  try {
    event = JSON.parse(rawBody);
  } catch {
    return new Response("JSON invalide.", { status: 400 });
  }

  if (event.type !== "checkout.session.completed") {
    // On répond 200 pour les événements qu'on ignore, sinon Stripe les ré-essaie inutilement.
    return new Response("Événement ignoré.", { status: 200 });
  }

  const session = event.data.object;
  const sessionId: string = session.id;

  const orders = getStore("orders");

  // Idempotence : Stripe garantit une livraison "au moins une fois" et peut
  // renvoyer le même événement plusieurs fois (retry réseau côté Stripe, etc.).
  const existing = await orders.get(sessionId, { type: "json" });
  if (existing) {
    return new Response("Déjà traité.", { status: 200 });
  }

  const shippingDetails = session.customer_details?.address;
  const shippingAddress = shippingDetails
    ? [
        shippingDetails.line1,
        shippingDetails.line2,
        `${shippingDetails.postal_code ?? ""} ${shippingDetails.city ?? ""}`.trim(),
        shippingDetails.country,
      ]
        .filter(Boolean)
        .join(", ")
    : null;

  const order = {
    sessionId,
    createdAt: new Date().toISOString(),
    customerEmail: session.customer_details?.email ?? null,
    customerName: session.customer_details?.name ?? null,
    amountTotal: session.amount_total,
    currency: session.currency,
    shippingAddress,
    lines: parseCartSummary(session.metadata?.cart),
    status: "paid",
  };

  // On enregistre d'abord (source de vérité), l'email est un bonus non bloquant.
  await orders.setJSON(sessionId, order);
  await sendOrderEmail(order);

  return new Response("OK", { status: 200 });
};

export const config: Config = {
  path: "/.netlify/functions/stripe-webhook",
};
