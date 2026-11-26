import { useEffect, useMemo, useState } from 'react'
import {
  Activity,
  BadgeCheck,
  Boxes,
  Building2,
  ChevronRight,
  CircleDollarSign,
  Clock3,
  FileSpreadsheet,
  Gauge,
  Layers3,
  MapPinned,
  Radio,
  ShieldCheck,
  Sparkles,
  Store,
  Truck,
  Warehouse,
  Wifi,
  Zap,
} from 'lucide-react'

/** @typedef {'syncing' | 'compliance' | 'dispatched' | 'live'} HubStatus */

const STATUS_META = {
  syncing: {
    label: 'Syncing Catalog',
    tone: 'amber',
    className: 'bg-amber-400/10 text-amber-400 border-amber-400/25',
    Icon: Wifi,
  },
  compliance: {
    label: 'Compliance Verified',
    tone: 'emerald',
    className: 'bg-emerald-400/10 text-emerald-400 border-emerald-400/25',
    Icon: ShieldCheck,
  },
  dispatched: {
    label: 'Dispatched to Dark Store',
    tone: 'indigo',
    className: 'bg-indigo-400/10 text-indigo-300 border-indigo-400/25',
    Icon: Truck,
  },
  live: {
    label: 'Live Fulfillment',
    tone: 'emerald',
    className: 'bg-emerald-400/10 text-emerald-400 border-emerald-400/25',
    Icon: Radio,
  },
}

const HUBS = [
  {
    id: 'gourmet',
    name: 'Gourmet Bakery Hub',
    city: 'Bengaluru · Koramangala',
    skus: 1840,
    status: 'syncing',
    channel: 'Blinkit + Zepto',
    metrics: {
      latencyMs: 412,
      invoiceSpeed: 'Instant',
      mismatchPct: 0.031,
      slaPct: 98.6,
      darkStores: 6,
      ordersToday: 4820,
      catalogHealth: 96.2,
    },
  },
  {
    id: 'dairy',
    name: 'Organic Dairy Express',
    city: 'Mumbai · Andheri East',
    skus: 620,
    status: 'compliance',
    channel: 'Instamart',
    metrics: {
      latencyMs: 288,
      invoiceSpeed: 'Instant',
      mismatchPct: 0.012,
      slaPct: 99.4,
      darkStores: 9,
      ordersToday: 7150,
      catalogHealth: 99.1,
    },
  },
  {
    id: 'fresh',
    name: 'FreshCut Produce Grid',
    city: 'Delhi NCR · Gurugram',
    skus: 2410,
    status: 'dispatched',
    channel: 'Zepto + Swiggy',
    metrics: {
      latencyMs: 356,
      invoiceSpeed: 'Instant',
      mismatchPct: 0.048,
      slaPct: 97.9,
      darkStores: 14,
      ordersToday: 11240,
      catalogHealth: 94.8,
    },
  },
  {
    id: 'pharma',
    name: 'CareRx Micro-Pharmacy',
    city: 'Hyderabad · Hitech City',
    skus: 980,
    status: 'live',
    channel: 'Tata Neu + Zepto',
    metrics: {
      latencyMs: 241,
      invoiceSpeed: 'Instant',
      mismatchPct: 0.008,
      slaPct: 99.7,
      darkStores: 5,
      ordersToday: 3290,
      catalogHealth: 99.6,
    },
  },
  {
    id: 'pet',
    name: 'PawSupply Dark Lane',
    city: 'Pune · Baner',
    skus: 1120,
    status: 'syncing',
    channel: 'Blinkit',
    metrics: {
      latencyMs: 438,
      invoiceSpeed: '< 800ms',
      mismatchPct: 0.055,
      slaPct: 97.1,
      darkStores: 4,
      ordersToday: 1960,
      catalogHealth: 92.4,
    },
  },
  {
    id: 'home',
    name: 'UrbanHome Essentials',
    city: 'Chennai · OMR',
    skus: 3200,
    status: 'live',
    channel: 'Instamart + Zepto',
    metrics: {
      latencyMs: 305,
      invoiceSpeed: 'Instant',
      mismatchPct: 0.019,
      slaPct: 98.9,
      darkStores: 11,
      ordersToday: 8640,
      catalogHealth: 97.8,
    },
  },
]

const VOLUME_TIERS = [
  { id: 'pilot', label: 'Pilot', ordersPerDay: 25000, takeRateBps: 18, hubs: 12 },
  { id: 'growth', label: 'Growth', ordersPerDay: 142850, takeRateBps: 22, hubs: 86 },
  { id: 'scale', label: 'Scale', ordersPerDay: 480000, takeRateBps: 26, hubs: 240 },
  { id: 'nation', label: 'Nation', ordersPerDay: 1200000, takeRateBps: 28, hubs: 620 },
]

function formatInt(n) {
  return new Intl.NumberFormat('en-IN').format(Math.round(n))
}

function formatInrCr(n) {
  if (n >= 100) return `₹${(n / 100).toFixed(2)} Cr`
  return `₹${n.toFixed(1)} L`
}

function StatusBadge({ status }) {
  const meta = STATUS_META[status]
  const Icon = meta.Icon
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] font-medium tracking-wide ${meta.className}`}
    >
      <Icon className="h-3 w-3" strokeWidth={2.25} />
      {meta.label}
    </span>
  )
}

function MetricTile({ icon: Icon, label, value, hint, accent = 'indigo' }) {
  const accents = {
    indigo: 'from-indigo-500/15 to-transparent text-indigo-300',
    emerald: 'from-emerald-500/15 to-transparent text-emerald-300',
    amber: 'from-amber-500/15 to-transparent text-amber-300',
    violet: 'from-violet-500/15 to-transparent text-violet-300',
  }
  return (
    <article className="group relative overflow-hidden rounded-xl border border-slate-800 bg-slate-950/70 p-4 transition hover:border-slate-700">
      <div className={`pointer-events-none absolute inset-0 bg-gradient-to-br ${accents[accent]} opacity-80`} />
      <div className="relative flex items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-slate-500">{label}</p>
          <p className="mt-2 font-display text-2xl font-bold tracking-tight text-slate-50">{value}</p>
          {hint ? <p className="mt-1.5 text-xs text-slate-400">{hint}</p> : null}
        </div>
        <div className="rounded-lg border border-slate-800 bg-slate-900/80 p-2 text-slate-300">
          <Icon className="h-4 w-4" />
        </div>
      </div>
    </article>
  )
}

function HubCard({ hub, active, onSelect }) {
  return (
    <button
      type="button"
      onClick={() => onSelect(hub.id)}
      className={`w-full rounded-xl border p-3.5 text-left transition duration-200 ${
        active
          ? 'border-indigo-500/50 bg-indigo-500/10 shadow-glow'
          : 'border-slate-800 bg-slate-950/50 hover:border-slate-700 hover:bg-slate-900/60'
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Store className={`h-3.5 w-3.5 shrink-0 ${active ? 'text-indigo-300' : 'text-slate-500'}`} />
            <h3 className="truncate font-display text-sm font-semibold text-slate-100">{hub.name}</h3>
          </div>
          <p className="mt-1 flex items-center gap-1 text-[11px] text-slate-500">
            <MapPinned className="h-3 w-3" />
            {hub.city}
          </p>
        </div>
        <ChevronRight className={`h-4 w-4 shrink-0 ${active ? 'text-indigo-300' : 'text-slate-600'}`} />
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <StatusBadge status={hub.status} />
        <span className="rounded-full border border-slate-800 px-2 py-0.5 font-mono text-[10px] text-slate-400">
          {formatInt(hub.skus)} SKUs
        </span>
      </div>
      <p className="mt-2.5 text-[11px] text-slate-500">{hub.channel}</p>
    </button>
  )
}

function ThroughputCounter({ base }) {
  const [value, setValue] = useState(base)

  useEffect(() => {
    setValue(base)
    const id = setInterval(() => {
      setValue((v) => v + Math.floor(Math.random() * 7) + 1)
    }, 1800)
    return () => clearInterval(id)
  }, [base])

  return (
    <span className="font-mono text-sm font-semibold tabular-nums text-indigo-200">
      {formatInt(value)}
    </span>
  )
}

function RoiCalculator({ tierIndex, onTierChange }) {
  const tier = VOLUME_TIERS[tierIndex]
  const aov = 380
  const gmvMonth = tier.ordersPerDay * 30 * aov
  const mrr = (gmvMonth * tier.takeRateBps) / 10000
  const mrrLakh = mrr / 100000

  return (
    <section className="rounded-xl border border-slate-800 bg-slate-950/70 p-4">
      <div className="flex items-center justify-between gap-2">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-slate-500">ROI / MRR model</p>
          <h3 className="mt-1 font-display text-base font-semibold text-slate-100">Transaction volume tier</h3>
        </div>
        <CircleDollarSign className="h-4 w-4 text-violet-300" />
      </div>

      <div className="mt-4 grid grid-cols-4 gap-1.5">
        {VOLUME_TIERS.map((t, i) => (
          <button
            key={t.id}
            type="button"
            onClick={() => onTierChange(i)}
            className={`rounded-lg border px-2 py-2 text-center text-[11px] font-medium transition ${
              i === tierIndex
                ? 'border-violet-400/50 bg-violet-500/15 text-violet-200'
                : 'border-slate-800 bg-slate-900/40 text-slate-400 hover:border-slate-700'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <label className="mt-4 block">
        <div className="mb-2 flex items-center justify-between text-[11px] text-slate-500">
          <span>Volume slider</span>
          <span className="font-mono text-slate-300">{tier.label}</span>
        </div>
        <input
          type="range"
          min={0}
          max={VOLUME_TIERS.length - 1}
          step={1}
          value={tierIndex}
          onChange={(e) => onTierChange(Number(e.target.value))}
          className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-slate-800 accent-violet-400"
        />
      </label>

      <dl className="mt-4 space-y-2.5 border-t border-slate-800 pt-3">
        <div className="flex justify-between gap-3 text-xs">
          <dt className="text-slate-500">Orders / day</dt>
          <dd className="font-mono text-slate-200">{formatInt(tier.ordersPerDay)}</dd>
        </div>
        <div className="flex justify-between gap-3 text-xs">
          <dt className="text-slate-500">Active retail hubs</dt>
          <dd className="font-mono text-slate-200">{tier.hubs}</dd>
        </div>
        <div className="flex justify-between gap-3 text-xs">
          <dt className="text-slate-500">Take rate</dt>
          <dd className="font-mono text-slate-200">{(tier.takeRateBps / 100).toFixed(2)}%</dd>
        </div>
        <div className="flex justify-between gap-3 text-xs">
          <dt className="text-slate-500">Assumed AOV</dt>
          <dd className="font-mono text-slate-200">₹{aov}</dd>
        </div>
        <div className="mt-1 flex items-end justify-between gap-3 rounded-lg border border-violet-500/20 bg-violet-500/10 px-3 py-2.5">
          <dt className="text-[11px] uppercase tracking-[0.12em] text-violet-300/80">Est. MRR</dt>
          <dd className="font-display text-xl font-bold text-violet-100">{formatInrCr(mrrLakh)}</dd>
        </div>
      </dl>
      <p className="mt-3 text-[10px] leading-relaxed text-slate-500">
        Illustrative SaaS + take-rate hybrid on GMV. Not a forecast — prototype economics for investor walkthroughs.
      </p>
    </section>
  )
}

/**
 * Zepstream — investor-facing interactive prototype.
 * Self-contained React component for B2B q-commerce middleware narrative.
 */
export default function ZepstreamPrototype() {
  const [activeHubId, setActiveHubId] = useState(HUBS[1].id)
  const [tierIndex, setTierIndex] = useState(1)
  const [clock, setClock] = useState(() => new Date())

  const activeHub = useMemo(
    () => HUBS.find((h) => h.id === activeHubId) ?? HUBS[0],
    [activeHubId],
  )

  useEffect(() => {
    const id = setInterval(() => setClock(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  const m = activeHub.metrics
  const latencyTone = m.latencyMs < 350 ? 'emerald' : m.latencyMs < 450 ? 'indigo' : 'amber'
  const mismatchTone = m.mismatchPct <= 0.02 ? 'emerald' : m.mismatchPct <= 0.04 ? 'indigo' : 'amber'

  return (
    <div className="mesh-bg min-h-screen">
      <div className="pointer-events-none fixed inset-0 grid-fade" aria-hidden />

      <div className="relative mx-auto flex min-h-screen max-w-[1600px] flex-col px-4 py-4 md:px-6 md:py-5">
        {/* Header strip */}
        <header className="flex flex-col gap-3 rounded-2xl border border-slate-800 bg-slate-950/80 px-4 py-3 shadow-glow backdrop-blur-md sm:flex-row sm:items-center sm:justify-between sm:px-5">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-indigo-500/30 bg-gradient-to-br from-indigo-500/30 to-violet-600/20">
              <Zap className="h-5 w-5 text-indigo-200" strokeWidth={2.25} />
            </div>
            <div>
              <h1 className="font-display text-xl font-extrabold tracking-tight text-slate-50 sm:text-2xl">
                Zepstream
              </h1>
              <p className="text-[11px] text-slate-500">
                Pure-software middleware · Indian quick-commerce fulfillment
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2 sm:gap-3">
            <div className="inline-flex items-center gap-2 rounded-full border border-emerald-500/25 bg-emerald-500/10 px-3 py-1.5 text-xs text-emerald-300 shadow-glow-emerald">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400" />
              </span>
              Live Global System Status: Healthy
            </div>

            <div className="inline-flex items-center gap-2 rounded-full border border-slate-800 bg-slate-900/80 px-3 py-1.5 text-xs text-slate-300">
              <Activity className="h-3.5 w-3.5 text-indigo-300" />
              <span className="text-slate-500">Total Throughput:</span>
              <ThroughputCounter base={142850} />
              <span className="text-slate-500">Orders/Day</span>
            </div>

            <div className="hidden items-center gap-1.5 rounded-full border border-slate-800 px-3 py-1.5 font-mono text-[11px] text-slate-500 lg:inline-flex">
              <Clock3 className="h-3.5 w-3.5" />
              {clock.toLocaleTimeString('en-IN', { hour12: false })} IST
            </div>
          </div>
        </header>

        {/* Main grid */}
        <main className="mt-4 grid flex-1 grid-cols-1 gap-4 xl:grid-cols-12 xl:gap-5">
          {/* Left — Live pipeline feed */}
          <aside className="flex min-h-0 flex-col rounded-2xl border border-slate-800 bg-slate-950/60 xl:col-span-3">
            <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
              <div>
                <p className="text-[10px] font-medium uppercase tracking-[0.16em] text-slate-500">
                  Live pipeline
                </p>
                <h2 className="font-display text-sm font-semibold text-slate-100">Retail onboarding feed</h2>
              </div>
              <Layers3 className="h-4 w-4 text-slate-500" />
            </div>
            <div className="flex-1 space-y-2.5 overflow-y-auto p-3" role="list">
              {HUBS.map((hub, i) => (
                <div
                  key={hub.id}
                  className="animate-slideIn"
                  style={{ animationDelay: `${i * 40}ms` }}
                  role="listitem"
                >
                  <HubCard
                    hub={hub}
                    active={hub.id === activeHubId}
                    onSelect={setActiveHubId}
                  />
                </div>
              ))}
            </div>
            <div className="border-t border-slate-800 px-4 py-2.5 text-[10px] text-slate-500">
              Click a hub to drive the dispatch canvas →
            </div>
          </aside>

          {/* Center — Automated dispatch engine */}
          <section className="flex min-h-0 flex-col rounded-2xl border border-slate-800 bg-slate-950/60 xl:col-span-6">
            <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-800 px-4 py-3 sm:px-5">
              <div>
                <p className="text-[10px] font-medium uppercase tracking-[0.16em] text-indigo-300/80">
                  Automated dispatch engine
                </p>
                <h2 className="mt-0.5 font-display text-lg font-bold tracking-tight text-slate-50">
                  {activeHub.name}
                </h2>
                <p className="mt-1 text-xs text-slate-500">
                  {activeHub.city} · {activeHub.channel}
                </p>
              </div>
              <StatusBadge status={activeHub.status} />
            </div>

            <div className="flex-1 space-y-4 overflow-y-auto p-4 sm:p-5">
              <div className="grid gap-3 sm:grid-cols-3" key={activeHub.id}>
                <MetricTile
                  icon={Gauge}
                  label="Micro-fulfillment sync"
                  value={`< ${m.latencyMs}ms`}
                  hint="Catalog ↔ dark-store latency"
                  accent={latencyTone}
                />
                <MetricTile
                  icon={FileSpreadsheet}
                  label="Invoice generation"
                  value={m.invoiceSpeed}
                  hint="GST-ready e-invoice pipeline"
                  accent="emerald"
                />
                <MetricTile
                  icon={Boxes}
                  label="Catalog mismatch"
                  value={`${m.mismatchPct.toFixed(3)}%`}
                  hint="SKU parity across channels"
                  accent={mismatchTone}
                />
              </div>

              {/* Visual pipeline */}
              <div className="rounded-xl border border-slate-800 bg-gradient-to-b from-slate-900/80 to-slate-950/80 p-4">
                <p className="mb-4 text-[10px] font-medium uppercase tracking-[0.16em] text-slate-500">
                  Operational lineage · this hub
                </p>
                <div className="grid gap-2 sm:grid-cols-4">
                  {[
                    { Icon: Store, title: 'Retail catalog', sub: `${formatInt(activeHub.skus)} SKUs` },
                    { Icon: ShieldCheck, title: 'Compliance layer', sub: 'FSSAI · GST · weights' },
                    { Icon: Warehouse, title: 'Dark-store sync', sub: `${m.darkStores} nodes` },
                    { Icon: Truck, title: 'Dispatch ready', sub: `${formatInt(m.ordersToday)} today` },
                  ].map((step, idx) => (
                    <div key={step.title} className="relative">
                      <div className="flex h-full flex-col rounded-lg border border-slate-800 bg-slate-950/70 p-3">
                        <step.Icon className="h-4 w-4 text-indigo-300" />
                        <p className="mt-2 text-xs font-semibold text-slate-200">{step.title}</p>
                        <p className="mt-1 font-mono text-[10px] text-slate-500">{step.sub}</p>
                      </div>
                      {idx < 3 ? (
                        <div className="pointer-events-none absolute -right-1 top-1/2 hidden -translate-y-1/2 text-slate-600 sm:block">
                          <ChevronRight className="h-3.5 w-3.5" />
                        </div>
                      ) : null}
                    </div>
                  ))}
                </div>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <article className="rounded-xl border border-slate-800 bg-slate-950/70 p-4">
                  <div className="flex items-center gap-2 text-slate-400">
                    <BadgeCheck className="h-4 w-4 text-emerald-400" />
                    <span className="text-[11px] uppercase tracking-[0.12em]">SLA adherence</span>
                  </div>
                  <p className="mt-2 font-display text-3xl font-bold text-emerald-300">{m.slaPct}%</p>
                  <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-800">
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-emerald-500 to-teal-300 transition-all duration-500"
                      style={{ width: `${m.slaPct}%` }}
                    />
                  </div>
                </article>
                <article className="rounded-xl border border-slate-800 bg-slate-950/70 p-4">
                  <div className="flex items-center gap-2 text-slate-400">
                    <Sparkles className="h-4 w-4 text-amber-300" />
                    <span className="text-[11px] uppercase tracking-[0.12em]">Catalog health</span>
                  </div>
                  <p className="mt-2 font-display text-3xl font-bold text-slate-100">{m.catalogHealth}%</p>
                  <p className="mt-2 text-xs text-slate-500">
                    Attribute completeness · image QC · price parity across marketplaces
                  </p>
                </article>
              </div>

              <p className="rounded-lg border border-dashed border-slate-800 bg-slate-900/30 px-3 py-2 text-[11px] leading-relaxed text-slate-500">
                Zepstream sits between brand / multi-brand retailers and q-commerce dark stores — automating
                catalog sync, compliance packets, and dispatch instructions without owning warehouses.
              </p>
            </div>
          </section>

          {/* Right — Investor metrics */}
          <aside className="flex min-h-0 flex-col gap-3 xl:col-span-3">
            <section className="rounded-2xl border border-slate-800 bg-slate-950/60 p-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-[10px] font-medium uppercase tracking-[0.16em] text-slate-500">
                    Investor metrics
                  </p>
                  <h2 className="font-display text-sm font-semibold text-slate-100">Asset-light thesis</h2>
                </div>
                <Building2 className="h-4 w-4 text-slate-500" />
              </div>

              <div className="mt-4 space-y-3">
                <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-3">
                  <div className="flex items-center gap-2">
                    <Warehouse className="h-4 w-4 text-emerald-400" />
                    <p className="text-[11px] uppercase tracking-[0.12em] text-emerald-300/90">
                      Asset-light scale
                    </p>
                  </div>
                  <p className="mt-2 font-display text-xl font-bold text-slate-50">Zero warehouses</p>
                  <p className="mt-1 text-xs text-slate-500">
                    Software margin profile — partners own dark-store real estate & inventory risk.
                  </p>
                </div>

                <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-3">
                  <p className="text-[11px] uppercase tracking-[0.12em] text-slate-500">India q-commerce TAM</p>
                  <p className="mt-2 font-display text-xl font-bold text-indigo-200">₹1.2L Cr+</p>
                  <p className="mt-1 text-xs leading-relaxed text-slate-500">
                    Tailwinds: 10–15 min delivery density, multi-brand onboarding, GST e-invoice enforcement,
                    and SKU explosion across metros + Tier-1.
                  </p>
                </div>

                <ul className="space-y-2 text-xs text-slate-400">
                  <li className="flex gap-2">
                    <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-indigo-400" />
                    High-volume retail onboarding without ops headcount linear growth
                  </li>
                  <li className="flex gap-2">
                    <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-violet-400" />
                    Compliance + invoice automation as sticky middleware wedge
                  </li>
                  <li className="flex gap-2">
                    <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-emerald-400" />
                    Network effects across dark-store density & brand catalogs
                  </li>
                </ul>
              </div>
            </section>

            <RoiCalculator tierIndex={tierIndex} onTierChange={setTierIndex} />
          </aside>
        </main>

        <footer className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-slate-800/80 pt-3 text-[10px] text-slate-600">
          <span>Zepstream investor prototype · illustrative data</span>
          <span className="inline-flex items-center gap-1">
            <Zap className="h-3 w-3" /> Middleware for dark stores & micro-fulfillment
          </span>
        </footer>
      </div>
    </div>
  )
}
