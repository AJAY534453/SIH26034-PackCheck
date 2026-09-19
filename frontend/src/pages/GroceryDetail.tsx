import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../services/api'
import { Card } from '../components/Badges'
import { Confidence, EmptyState, Skeleton, StatusBadge, useToast } from '../components/ui'
import { Icon } from '../components/Icon'
import type { GroceryDetail as GroceryDetailData } from '../types'

const FIELD_LABELS: Record<string, string> = {
  product_name: 'Product Name',
  brand: 'Brand',
  common_name: 'Common Name',
  category: 'Category',
  manufacturer: 'Manufacturer',
  manufacturer_address: 'Manufacturer Address',
  packer: 'Packer',
  importer: 'Importer',
  marketer: 'Marketed By',
  country_of_origin: 'Country of Origin',
  net_quantity: 'Net Quantity',
  mrp: 'MRP',
  unit_sale_price: 'Unit Sale Price',
  date_manufacturing: 'Manufacturing Date',
  date_packing: 'Packing Date',
  date_expiry: 'Expiry / Use By',
  date_best_before: 'Best Before',
  batch_lot: 'Batch / Lot',
  consumer_care_phone: 'Consumer Care Phone',
  consumer_care_email: 'Consumer Care Email',
  website: 'Website',
  fssai_license: 'FSSAI Licence',
}

const BASIS_TEXT: Record<string, string> = {
  PRINTED_ON_PACKAGE: 'printed on the package',
  DURATION_FROM_MANUFACTURING_DATE: 'counted from the manufacturing date (provisional)',
  DURATION_ONLY_NO_ANCHOR: 'a duration was declared but no date to count it from',
  NOT_DECLARED: 'no expiry declaration found in the evidence',
  NOT_PROVIDED: 'not provided',
}

const ALERT_TONE: Record<string, string> = {
  EXPIRED: 'NON_COMPLIANT',
  EXPIRING_SOON: 'UNCERTAIN',
  FRESH: 'PASS',
  NO_EXPIRY_DATA: 'NOT_APPLICABLE',
}

/** Product Details — reached by selecting a product card, never shown up front. */
export default function GroceryDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [data, setData] = useState<GroceryDetailData | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const { show, node: toast } = useToast()

  useEffect(() => {
    if (!id) return
    api
      .groceryItem(Number(id))
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : 'Load failed'))
      .finally(() => setLoading(false))
  }, [id])

  async function remove() {
    if (!data) return
    setBusy(true)
    try {
      await api.del(`/grocery/${data.item.id}`)
      show('Removed from grocery tracker')
      navigate('/grocery')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not remove the item')
    } finally {
      setBusy(false)
    }
  }

  async function raiseComplaint() {
    if (!data) return
    setBusy(true)
    try {
      await api.post('/complaints', {
        inspection_id: data.item.inspection_id,
        product_id: data.item.product_id,
        product_name: data.item.product_name,
        category: 'EXPIRY',
        severity: data.item.alert === 'EXPIRED' ? 'HIGH' : 'MEDIUM',
        issue: `Grocery item "${data.item.product_name}" is ${data.item.alert.toLowerCase().replace(/_/g, ' ')}. ${data.item.detail}`,
      })
      show('Complaint created from this product')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not create the complaint')
    } finally {
      setBusy(false)
    }
  }

  if (loading) return <Card><Skeleton lines={6} /></Card>
  if (error && !data) return <div className="alert error">{error}</div>
  if (!data) return null

  const { item, product } = data
  const scan = product.scan

  return (
    <>
      <div className="page-head">
        <div className="grow">
          <div className="breadcrumb muted">
            <Link to="/grocery">Grocery</Link> <span aria-hidden="true">›</span> Product details
          </div>
          <div className="page-title">{item.product_name}</div>
          <div className="page-sub">
            {item.brand || 'Brand not recorded'} · {item.category || scan?.category || 'Uncategorised'}
          </div>
        </div>
        <div className="actions">
          <button className="btn secondary" onClick={raiseComplaint} disabled={busy}>
            <Icon name="message-square-warning" /> Raise complaint
          </button>
          <button className="btn secondary" onClick={() => navigate('/grocery')}>Back to list</button>
        </div>
      </div>

      {error && <div className="alert error">{error}</div>}
      {toast}

      <div className="grid cols-3" style={{ alignItems: 'start' }}>
        <Card title="Product image">
          {item.image_url ? (
            <img className="evidence-img" src={`/api${item.image_url}`} alt={item.product_name} />
          ) : (
            <EmptyState glyph="◌" headline="No image captured" how="This product was added manually, so there is no package photograph attached." />
          )}
        </Card>

        <Card title="Summary">
          <dl className="kv">
            <dt>Product</dt><dd>{item.product_name || '—'}</dd>
            <dt>Brand</dt><dd>{item.brand || '—'}</dd>
            <dt>Category</dt><dd>{item.category || scan?.category || '—'}</dd>
            <dt>Quantity</dt><dd>{item.quantity || '—'}</dd>
            <dt>MRP</dt><dd>{item.mrp ? `₹${item.mrp}` : '—'}</dd>
            <dt>Batch / Lot</dt><dd className="mono">{item.batch_lot || '—'}</dd>
          </dl>
        </Card>

        <Card title="Expiry tracking">
          <dl className="kv">
            <dt>Status</dt>
            <dd>
              <span className={`badge ${ALERT_TONE[item.alert] ?? 'NOT_APPLICABLE'}`}>
                <span className="bdot" aria-hidden="true">●</span>
                {item.alert.replace(/_/g, ' ')}
              </span>
            </dd>
            <dt>Expiry date</dt><dd>{item.expiry_date || 'not tracked'}</dd>
            <dt>Days remaining</dt><dd>{item.days_remaining != null ? item.days_remaining : '—'}</dd>
            <dt>Basis</dt><dd>{BASIS_TEXT[item.expiry_basis] ?? item.expiry_basis}</dd>
            <dt>Purchased</dt><dd>{item.purchase_date || '—'}</dd>
          </dl>
          <p className="muted" style={{ marginTop: 8, fontSize: 12 }}>{item.detail}</p>
        </Card>
      </div>

      <Card title="Declarations read from the package">
        {product.fields.length === 0 ? (
          <EmptyState
            glyph="◌"
            headline="No scan data linked"
            how="This item was added manually. Add it from a scan to attach the declarations read from the package."
          />
        ) : (
          <table className="tbl">
            <thead><tr><th>Field</th><th>Value</th><th>State</th><th>Confidence</th></tr></thead>
            <tbody>
              {product.fields.map((f) => (
                <tr key={f.field_name}>
                  <td><b>{FIELD_LABELS[f.field_name] ?? f.field_name.replace(/_/g, ' ')}</b></td>
                  <td>
                    {f.display_value || '—'}
                    {f.note && <div className="muted" style={{ fontSize: 11.5 }}>{f.note}</div>}
                  </td>
                  <td><StatusBadge value={f.state} /></td>
                  <td>{f.display_value ? <Confidence value={f.confidence} /> : <span className="muted">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <div className="grid cols-2">
        <Card title="Detected text">
          {product.detected_text ? (
            <pre className="ocr-text">{product.detected_text}</pre>
          ) : (
            <p className="muted">No OCR text is stored for this item's scan.</p>
          )}
        </Card>
        <Card title="Scan information">
          {scan ? (
            <dl className="kv">
              <dt>Inspection</dt>
              <dd><Link to={`/inspections/${scan.inspection_id}`} className="mono">{scan.inspection_number}</Link></dd>
              <dt>Scanned by</dt><dd>{scan.inspector || '—'}</dd>
              <dt>Scanned at</dt><dd>{scan.created_at.slice(0, 19).replace('T', ' ')}</dd>
              <dt>Status</dt><dd>{scan.status}</dd>
              <dt>Decision</dt><dd><StatusBadge value={scan.decision} /></dd>
              <dt>Category</dt><dd>{scan.category || '—'}</dd>
              <dt>Image quality</dt>
              <dd><StatusBadge value={scan.overall_quality} /> <Confidence value={scan.overall_quality_score} showMark={false} /></dd>
              <dt>Images</dt><dd>{scan.image_count}</dd>
            </dl>
          ) : (
            <p className="muted">No scan is linked to this item.</p>
          )}
        </Card>
      </div>

      <Card title="Manage">
        <div className="flex">
          <button className="btn secondary" onClick={remove} disabled={busy}>
            <Icon name="trash" /> Remove from tracker
          </button>
        </div>
      </Card>
    </>
  )
}
