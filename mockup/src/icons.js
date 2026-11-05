const paths = {
  chart: '<path d="M4 19V5m0 14h16M8 15v-4m4 4V7m4 8V9"/>',
  pen: '<path d="m15 4 5 5M4 20l5-1L20 8a2 2 0 0 0-5-5L4 14z"/>',
  factory: '<path d="M3 21V10l6 4V8l6 4V3h5v18zM7 18h1m4 0h1m4 0h1"/>',
  cart: '<path d="M2 3h3l3 12h11l3-9H6M9 20h.01M18 20h.01"/>',
  chat: '<path d="M21 11a9 9 0 0 1-9 9 10 10 0 0 1-4-1l-6 2 2-6a9 9 0 1 1 17-4z"/>',
  calendar:
    '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v5m10-5v5M3 11h18"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  board:
    '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18m6-18v18M5 7h2m4 0h2m4 0h2"/>',
  wallet:
    '<path d="M20 8V5H5a2 2 0 0 0 0 4h16v11H5a2 2 0 0 1-2-2V7m18 7h-5v3h5"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
  refresh:
    '<path d="M20 7v5h-5M4 17v-5h5M5 8a8 8 0 0 1 13-3l2 3M4 16l2 3a8 8 0 0 0 13-3"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  back: '<path d="m14 6-6 6 6 6"/>',
  next: '<path d="m9 6 6 6-6 6"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21v-3a8 8 0 0 1 16 0v3"/>',
  box: '<path d="m12 3 9 5-9 5-9-5zM3 8v10l9 5 9-5V8M12 13v10"/>',
  link: '<path d="m10 13 4-4M8 15l-2 2a3 3 0 0 1-4-4l5-5a3 3 0 0 1 4 0m2 3a3 3 0 0 1 4 0l5-5a3 3 0 0 0-4-4l-2 2"/>',
  pin: '<path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0z"/><circle cx="12" cy="10" r="2"/>',
  phone: '<path d="m5 3 4 4-2 3a15 15 0 0 0 7 7l3-2 4 4c-5 8-21-8-16-16z"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  send: '<path d="m22 2-7 20-4-9L2 9zM11 13 22 2"/>',
  file: '<path d="M14 2H5v20h14V7zM14 2v6h5M8 12h8M8 16h8"/>',
  attach: '<path d="m8 12 6-6a3 3 0 0 1 4 4l-8 8a5 5 0 0 1-7-7l9-9a2 2 0 0 1 3 3l-9 9a1 1 0 0 0 2 2l8-8"/>',
  copy: '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
  download: '<path d="M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5"/>',
  bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
  alert: '<path d="m12 3 10 18H2zM12 9v5m0 3v.01"/>',
  bookmark: '<path d="M5 3h14v19l-7-5-7 5z"/>',
  settings:
    '<path d="m9 3-1 3-3 1-2 4 2 2-1 3 4 3 3-1 2 3 4-2 1-3 3-1v-5l-3-1-1-4z"/><circle cx="12" cy="12" r="3"/>',
};
export function icon(name, cls = "") {
  return `<svg class="icon ${cls}" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.file}</svg>`;
}
