import type { Context, Config } from "@netlify/functions";
import { products } from "./products-data.mts";

type CartItem = { id: string; qty: number };

/**
 * Encode un objet (potentiellement imbriqué / avec tableaux) au format
 * x-www-form-urlencoded avec notation à crochets attendue par l'API Stripe
 * (ex: line_items[0][price_data][unit_amount]=1990).
 * Évite toute dépendance au package npm "stripe" (registre npm inaccessible
 * depuis cet environnement de build) — on parle directement à l'API REST.
 */
function stripeEncode(obj: unknown, prefix = ""): string[] {
  const parts: string[] = [];
  if (Array.isArray(obj)) {
    obj.forEach((v, i) => parts.push(...stripeEncode(v, `${prefix}[${i}]`)));
  } else if (obj !== null && typeof obj === "object") {
    for (const [key, value] of Object.entries(obj)) {
      const nextPrefix = prefix ? `${prefix}[${key}]` : key;
      parts.push(...stripeEncode(value, nextPrefix));
    }
  } else if (obj !== undefined && obj !== null) {
    parts.push(`${encodeURIComponent(prefix)}=${encodeURIComponent(String(obj))}`);
  }
  return parts;
}

export default async (req: Request, context: Context) => {
  if (req.method !== "POST") {
    return new Response(JSON.stringify({ error: "Méthode non autorisée." }), { status: 405 });
  }

  const secretKey = Netlify.env.get("STRIPE_SECRET_KEY");
  if (!secretKey) {
    return new Response(
      JSON.stringify({
        error:
          "Le paiement n'est pas encore configuré (clé Stripe manquante). Ajoutez STRIPE_SECRET_KEY dans les variables d'environnement Netlify.",
      }),
      { status: 501, headers: { "Content-Type": "application/json" } }
    );
  }

  let body: { items?: CartItem[] };
  try {
    body = await req.json();
  } catch {
    return new Response(JSON.stringify({ error: "Requête invalide." }), { status: 400 });
  }

  const items = Array.isArray(body.items) ? body.items : [];
  if (items.length === 0) {
    return new Response(JSON.stringify({ error: "Le panier est vide." }), { status: 400 });
  }

  const siteUrl = Netlify.env.get("URL") || new URL(req.url).origin;

  const line_items = [];
  for (const item of items) {
    const product = products.find((p) => p.id === item.id);
    const qty = Math.max(1, Math.min(50, Math.floor(Number(item.qty) || 1)));
    if (!product) continue;
    line_items.push({
      quantity: qty,
      price_data: {
        currency: "eur",
        unit_amount: product.price, // en centimes — relu depuis products-data.mts, jamais depuis le panier client
        product_data: {
          name: product.name,
          images: [`${siteUrl}/${product.image}`],
        },
      },
    });
  }

  if (line_items.length === 0) {
    return new Response(JSON.stringify({ error: "Aucun article valide dans le panier." }), { status: 400 });
  }

  const payload = {
    mode: "payment",
    line_items,
    shipping_address_collection: { allowed_countries: ["FR", "BE", "CH", "LU"] },
    success_url: `${siteUrl}/succes.html`,
    cancel_url: `${siteUrl}/annulation.html`,
  };

  try {
    const stripeRes = await fetch("https://api.stripe.com/v1/checkout/sessions", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${secretKey}`,
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: stripeEncode(payload).join("&"),
    });

    const session = await stripeRes.json();

    if (!stripeRes.ok) {
      const message = session?.error?.message || "Erreur Stripe inconnue.";
      return new Response(JSON.stringify({ error: message }), { status: 500 });
    }

    return new Response(JSON.stringify({ url: session.url }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Erreur réseau vers Stripe.";
    return new Response(JSON.stringify({ error: message }), { status: 500 });
  }
};

export const config: Config = {
  path: "/.netlify/functions/create-checkout",
};
