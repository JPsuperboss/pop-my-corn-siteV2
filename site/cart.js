/* Panier client — stocké en localStorage. Pas de données sensibles ici :
   uniquement des paires {id, qty}. Les prix réels sont toujours relus
   depuis products.json / vérifiés côté serveur au moment du paiement. */

const CART_KEY = "pixotile_cart_v1";

function getCart() {
  try {
    const raw = localStorage.getItem(CART_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch (e) {
    return [];
  }
}

function saveCart(cart) {
  try {
    localStorage.setItem(CART_KEY, JSON.stringify(cart));
  } catch (e) {
    /* stockage indisponible : le panier ne persistera pas, tant pis */
  }
  updateCartBadge();
}

function addToCart(id, qty = 1) {
  const cart = getCart();
  const existing = cart.find((i) => i.id === id);
  if (existing) {
    existing.qty += qty;
  } else {
    cart.push({ id, qty });
  }
  saveCart(cart);
}

function setQty(id, qty) {
  let cart = getCart();
  if (qty <= 0) {
    cart = cart.filter((i) => i.id !== id);
  } else {
    const existing = cart.find((i) => i.id === id);
    if (existing) existing.qty = qty;
  }
  saveCart(cart);
}

function removeFromCart(id) {
  const cart = getCart().filter((i) => i.id !== id);
  saveCart(cart);
}

function cartCount() {
  return getCart().reduce((sum, i) => sum + i.qty, 0);
}

function updateCartBadge() {
  document.querySelectorAll(".cart-count").forEach((el) => {
    el.textContent = cartCount();
  });
}

async function loadProducts() {
  const res = await fetch("products.json");
  const data = await res.json();
  // products.json peut être un tableau brut [...] ou un objet {"products": [...]}
  // selon la dernière source qui l'a écrit (édition GitHub directe vs panneau
  // d'administration) : on accepte les deux formats pour que la boutique ne
  // se retrouve jamais vide à cause de ça.
  return Array.isArray(data) ? data : (data.products || []);
}

function formatPrice(cents) {
  return (cents / 100).toLocaleString("fr-FR", { minimumFractionDigits: 2 }) + " €";
}

function starString(stars) {
  const full = "★".repeat(stars);
  const empty = `<span class="off">${"★".repeat(5 - stars)}</span>`;
  return full + empty;
}

document.addEventListener("DOMContentLoaded", updateCartBadge);
