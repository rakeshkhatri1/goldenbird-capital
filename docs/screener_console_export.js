/**
 * Screener.in — export all company slugs from a query results page (no Premium).
 *
 * 1. Open your screen (e.g. Market cap > 0), log in.
 * 2. DevTools → Console → type: allow pasting
 * 3. Paste this entire file and run.
 * 4. Saves screener_export.json (slug + name per row).
 * 5. Copy to project: data/screener_symbols.txt (one slug per line) OR
 *    data/screener_export.json for richer import later.
 */
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const rows = [];
  const seen = new Set();

  const maxPage =
    +document.body.innerText.match(/page \d+ of (\d+)/i)?.[1] || 108;
  const qs = location.search;
  const base = location.pathname + qs;

  const extract = (doc) => {
    doc.querySelectorAll('table tbody tr').forEach((tr) => {
      const a = tr.querySelector('a[href*="/company/"]');
      if (!a) return;
      const m = a.getAttribute('href').match(/\/company\/([^/]+)\//i);
      if (!m) return;
      const slug = m[1].toUpperCase();
      if (seen.has(slug)) return;
      seen.add(slug);
      rows.push({ slug, name: a.textContent.trim() });
    });
  };

  for (let page = 1; page <= maxPage; page++) {
    if (page === 1) {
      extract(document);
    } else {
      const url = `${location.origin}${base}${base.includes('?') ? '&' : '?'}page=${page}`;
      const html = await (await fetch(url, { credentials: 'include' })).text();
      extract(new DOMParser().parseFromString(html, 'text/html'));
    }
    console.log(`page ${page}/${maxPage}: ${rows.length} companies`);
    await sleep(1000);
  }

  const json = JSON.stringify({ exported_at: new Date().toISOString(), count: rows.length, companies: rows }, null, 2);
  const blob = new Blob([json], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'screener_export.json';
  a.click();

  const txt = rows.map((r) => r.slug).join('\n');
  const b2 = new Blob([txt], { type: 'text/plain' });
  const a2 = document.createElement('a');
  a2.href = URL.createObjectURL(b2);
  a2.download = 'screener_symbols.txt';
  a2.click();

  console.log(`Done — ${rows.length} companies (json + txt downloaded)`);
  return rows;
})();
