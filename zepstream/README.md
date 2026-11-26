# Zepstream — Investor Prototype

Interactive investor-facing dashboard for **Zepstream** (B2B middleware for Indian quick-commerce fulfillment).

## Run

```bash
cd zepstream
npm install --registry https://registry.npmjs.org/
npm run dev
```

Opens at [http://localhost:5174](http://localhost:5174).

(Optional) Production build: `npm run build` → `dist/`

## Structure

- `src/ZepstreamPrototype.jsx` — single self-contained React component (UI + interaction)
- Tailwind CSS + Lucide React

Separate from the NSE engine app.
