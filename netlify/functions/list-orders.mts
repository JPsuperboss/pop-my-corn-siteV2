import type { Context, Config } from "@netlify/functions";
import { getStore } from "@netlify/blobs";

/**
 * Endpoint protégé qui liste les commandes enregistrées par stripe-webhook.mts.
 * Protection volontairement simple (clé partagée en query string ou header) :
 * suffisant pour un usage interne à faible volume, à ne pas considérer comme
 * un vrai système d'authentification si le volume de commandes grossit.
 */
export default async (req: Request, context: Context) => {
  const adminKey = Netlify.env.get("ADMIN_KEY");
  if (!adminKey) {
    return new Response(JSON.stringify({ error: "ADMIN_KEY non configurée côté serveur." }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    });
  }

  const url = new URL(req.url);
  const providedKey = url.searchParams.get("key") || req.headers.get("x-admin-key");
  if (providedKey !== adminKey) {
    return new Response(JSON.stringify({ error: "Non autorisé." }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
  }

  const orders = getStore("orders");
  const { blobs } = await orders.list();

  const results = await Promise.all(blobs.map((b) => orders.get(b.key, { type: "json" })));

  const sorted = results
    .filter((o): o is Record<string, unknown> => o !== null)
    .sort((a, b) => ((a.createdAt as string) < (b.createdAt as string) ? 1 : -1));

  return new Response(JSON.stringify(sorted), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
};

export const config: Config = {
  path: "/.netlify/functions/list-orders",
};
