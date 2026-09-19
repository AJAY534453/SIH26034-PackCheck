import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../services/api'
import { Card } from '../components/Badges'
import { EmptyState, Skeleton, StatCard, useToast } from '../components/ui'
import { Icon } from '../components/Icon'
import type { GroceryCard, GroceryListResponse } from '../types'

/**
 * Grocery dashboard.
 *
 * After a scan the user sees a CLEAN, compact card per tracked product — image, name, category and
 * one status line. Everything else (declarations, detected text, scan provenance) lives on the
 * Product Details page reached by clicking a card (progressive disclosure).
 *
 * Expiry alerts exist ONLY for items the user explicitly added here; scanning never adds a product
 * automatically, which is why the empty state says so.
 */
const ALERT_BADGE: Record<string, { cls: string; glyph: string; text: string }> = {
  EXPIRED: { cls: 'NON_COMPLIANT', glyph: '✕', text: 'EXPIRED' },
  EXPIRING_SOON: { cls: 'UNCERTAIN', glyph: '⚠', text: 'EXPIRING SOON' },
  FRESH: { cls: 'PASS', glyph: '✓', text: 'FRESH' },
  NO_EXPIRY_DATA: { cls: 'NOT_APPLICABLE', glyph: '–', text: 'NO EXPIRY TRACKED' },
}

export default function Grocery() {
  const [data, setData] = useState<GroceryListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [showAdd, setShowAdd] = useState(false)
  const [form, setForm] = useState({
    product_name: '',
    brand: '',
    category: '',
    quantity: '',
    mrp: '',
    purchase_date: new Date().toISOString().slice(0, 10),
    expiry_date: '',
    best_before_text: '',
  })
  const { show, node: toast } = useToast()

  function load() {
    api
      .grocery()
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : 'Load failed'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  async function add() {
    if (!form.product_name.trim()) {
      setError('Enter the product name before adding it to the grocery tracker.')
      return
    }
    setBusy(true)
    setError('')
    try {
      const res = await api.post<{ message: string; expiry_note?: string }>('/grocery', form)
      show(res.expiry_note ? `Added — ${res.expiry_note}` : 'Added to grocery tracker')
      setForm({ ...form, product_name: '', brand: '', category: '', quantity: '', mrp: '', expiry_date: '', best_before_text: '' })
      setShowAdd(false)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not add the item')
    } finally {
      setBusy(false)
    }
  }

  const items = data?.items ?? []
  const alerts = data?.alerts ?? []

  return (
    <>
      <div className="page-head">
        <div className="grow">
          <div className="page-title">Grocery</div>
          <div className="page-sub">
            Products you chose to track. Only items added here raise expiry alerts — scanning a
            product never adds it automatically. Select a product to see its full details.
          </div>
        </div>
        <div className="actions">
          <Link className="btn secondary" to="/analysis">
            <Icon name="camera" /> Scan &amp; analyse
          </Link>
          <button className="btn" onClick={() => setShowAdd((v) => !v)}>
            <Icon name="plus" /> {showAdd ? 'Close' : 'Add a product'}
          </button>
        </div>
      </div>

      {error && <div className="alert error">{error}</div>}
      {toast}

      <div className="grid cols-4" style={{ marginBottom: 18 }}>
        <StatCard label="Tracked items" value={data?.total ?? 0} icon="shopping-cart" sub="explicitly added to the tracker" />
        <StatCard label="Expiry alerts" value={alerts.length} icon="bell" tone={alerts.length ? 'warn' : undefined} sub="expired or expiring within 7 days" />
      </div>

      {showAdd && (
        <Card title="Add a product to track">
          <div className="grid cols-3">
            <div className="field">
              <label htmlFor="g-name">Product name *</label>
              <input id="g-name" value={form.product_name} onChange={(e) => setForm({ ...form, product_name: e.target.value })} />
            </div>
            <div className="field">
              <label htmlFor="g-brand">Brand</label>
              <input id="g-brand" value={form.brand} onChange={(e) => setForm({ ...form, brand: e.target.value })} />
            </div>
            <div className="field">
              <label htmlFor="g-cat">Category</label>
              <input id="g-cat" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} placeholder="FOOD, BEVERAGE, COSMETIC…" />
            </div>
            <div className="field">
              <label htmlFor="g-qty">Quantity</label>
              <input id="g-qty" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} placeholder="500 g" />
            </div>
            <div className="field">
              <label htmlFor="g-mrp">MRP</label>
              <input id="g-mrp" value={form.mrp} onChange={(e) => setForm({ ...form, mrp: e.target.value })} placeholder="₹ 200" />
            </div>
            <div className="field">
              <label htmlFor="g-purchase">Purchase date</label>
              <input id="g-purchase" type="date" value={form.purchase_date} onChange={(e) => setForm({ ...form, purchase_date: e.target.value })} />
            </div>
            <div className="field">
              <label htmlFor="g-expiry">Expiry / best before (printed)</label>
              <input id="g-expiry" type="date" value={form.expiry_date} onChange={(e) => setForm({ ...form, expiry_date: e.target.value })} />
            </div>
            <div className="field">
              <label htmlFor="g-bb">Or the printed duration</label>
              <input id="g-bb" value={form.best_before_text} onChange={(e) => setForm({ ...form, best_before_text: e.target.value })} placeholder="Best before 12 months" />
            </div>
          </div>
          <button className="btn" onClick={add} disabled={busy}>
            <Icon name="plus" /> Add to grocery
          </button>
          <p className="muted" style={{ marginTop: 12, lineHeight: 1.5 }}>
            A duration such as “Best before 12 months” is only converted into a date when a date to
            count it from exists — anchoring it to the purchase date would be an invention, so the
            item is kept without a tracked expiry and you are told why.
          </p>
        </Card>
      )}

      {alerts.length > 0 && (
        <Card title="Expiry alerts" right={<span className="muted">{alerts.length} item(s)</span>}>
          {alerts.map((a) => (
            <div
              key={a.id}
              className={a.alert === 'EXPIRED' ? 'alert error' : 'alert warn'}
              style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}
            >
              <Icon name="alert-triangle" />
              <span style={{ flex: 1, minWidth: 180 }}>
                <b>{a.product_name}</b> — {a.detail}
                {a.expiry_basis === 'DURATION_FROM_MANUFACTURING_DATE' && (
                  <span className="muted"> (provisional: counted from the mfg date)</span>
                )}
              </span>
              <Link className="btn sm secondary" to={`/grocery/${a.id}`}>Details</Link>
            </div>
          ))}
        </Card>
      )}

      {loading ? (
        <Card><Skeleton lines={5} /></Card>
      ) : items.length === 0 ? (
        <Card>
          <EmptyState
            glyph="◌"
            headline="Nothing tracked yet"
            how="Add a product — or scan a package and add it from the results — to enable expiry tracking. Products are never added automatically."
            action={
              <div className="flex" style={{ justifyContent: 'center' }}>
                <button className="btn" onClick={() => setShowAdd(true)}><Icon name="plus" /> Add a product</button>
                <Link className="btn secondary" to="/analysis"><Icon name="camera" /> Scan an image</Link>
              </div>
            }
          />
        </Card>
      ) : (
        <div className="product-grid">
          {items.map((it) => <ProductCard key={it.id} item={it} />)}
        </div>
      )}
    </>
  )
}

function ProductCard({ item }: { item: GroceryCard }) {
  const badge = ALERT_BADGE[item.alert] ?? ALERT_BADGE.NO_EXPIRY_DATA
  const keyInfo =
    item.days_remaining != null
      ? `${item.days_remaining} day(s) left`
      : item.quantity || item.brand || 'no expiry date tracked'
  return (
    <Link className="product-card" to={`/grocery/${item.id}`} aria-label={`Open details for ${item.product_name}`}>
      <div className="thumb">
        {item.image_url
          ? <img src={`/api${item.image_url}`} alt={item.product_name} />
          : <span className="ph" aria-hidden="true"><Icon name="package" size={30} /></span>}
      </div>
      <div className="body">
        <div className="name">{item.product_name || 'Unnamed product'}</div>
        <div className="cat">{item.category || item.brand || 'Uncategorised'}</div>
        <div className="status">
          <span className={`badge ${badge.cls}`}>
            <span className="bdot" aria-hidden="true">{badge.glyph}</span>
            {badge.text}
          </span>
        </div>
        <div className="key">{keyInfo}</div>
        <div className="open-hint">View product details →</div>
      </div>
    </Link>
  )
}
