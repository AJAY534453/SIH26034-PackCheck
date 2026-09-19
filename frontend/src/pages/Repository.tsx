import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../services/api'
import { Card } from '../components/Badges'
import { EmptyState, Skeleton, StatCard, StatusChip } from '../components/ui'
import type { RepositoryList, ScanCard } from '../types'

const PAGE_SIZE = 12

const STATUS_LABEL: Record<string, string> = {
  AI_PRELIMINARY: 'AI preliminary',
  PENDING_FINALIZATION: 'Pending finalization',
  FINALIZED: 'Finalized',
  NOT_EVALUATED: 'Not reviewed',
}

const VERDICT_LABEL: Record<string, string> = {
  COMPLIANT: 'AI verdict: pass-oriented',
  NON_COMPLIANT: 'AI verdict: non-compliant',
  NEEDS_MANUAL_REVIEW: 'AI verdict: needs review',
}

export default function Repository() {
  const navigate = useNavigate()
  const [data, setData] = useState<RepositoryList | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [debounced, setDebounced] = useState('')
  const [reviewStatus, setReviewStatus] = useState('')
  const [verdict, setVerdict] = useState('')
  const [category, setCategory] = useState('')
  const [sort, setSort] = useState('recent')
  const [minScore, setMinScore] = useState('')
  const [maxScore, setMaxScore] = useState('')
  const [belowFloor, setBelowFloor] = useState(false)
  const [page, setPage] = useState(1)

  // Debounce the text search so typing does not fire a request per keystroke.
  useEffect(() => {
    const t = setTimeout(() => setDebounced(search), 300)
    return () => clearTimeout(t)
  }, [search])

  const load = useCallback(() => {
    setLoading(true)
    setError('')
    api
      .repository({
        q: debounced,
        review_status: reviewStatus,
        verdict,
        category,
        min_score: minScore === '' ? '' : minScore,
        max_score: maxScore === '' ? '' : maxScore,
        below_coverage_floor: belowFloor ? 'true' : '',
        sort,
        page,
        page_size: PAGE_SIZE,
      })
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : 'Could not load the repository'))
      .finally(() => setLoading(false))
  }, [debounced, reviewStatus, verdict, category, minScore, maxScore, belowFloor, sort, page])

  useEffect(() => {
    load()
  }, [load])

  const facets = data?.facets
  const statuses = useMemo(() => Object.entries(facets?.review_status ?? {}).sort((a, b) => b[1] - a[1]), [facets])
  const categories = useMemo(() => Object.entries(facets?.category ?? {}).sort((a, b) => b[1] - a[1]), [facets])
  const average = useMemo(() => {
    const items = data?.items ?? []
    if (!items.length) return null
    return Math.round((items.reduce((sum, s) => sum + (s.compliance_score ?? 0), 0) / items.length) * 10) / 10
  }, [data])
  const averageCoverage = useMemo(() => {
    const items = data?.items ?? []
    if (!items.length) return null
    return Math.round((items.reduce((sum, s) => sum + (s.coverage_score ?? 0), 0) / items.length) * 10) / 10
  }, [data])
  const threshold = data?.threshold ?? 85
  const coverageFloor = data?.coverage_floor ?? 70

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1

  function resetFilters() {
    setSearch('')
    setReviewStatus('')
    setVerdict('')
    setCategory('')
    setMinScore('')
    setMaxScore('')
    setBelowFloor(false)
    setSort('recent')
    setPage(1)
  }

  const activeFilters = Boolean(debounced || reviewStatus || verdict || category || minScore || maxScore || belowFloor)

  return (
    <>
      <div className="page-title">Compliance Repository</div>
      <div className="page-sub">
        Every scanned product with its stored evidence, checks, automated score and — separately — the
        official final decision. Nothing is presented as approved until an authorized official finalizes it.
      </div>

      <div className="stat-grid">
        <StatCard label="Scans in repository" value={data?.total ?? 0} icon="layers" />
        <StatCard
          label="Awaiting official finalization"
          value={data?.pending_finalization ?? 0}
          icon="clock"
          tone={(data?.pending_finalization ?? 0) > 0 ? 'warn' : 'ok'}
        />
        <StatCard label="Finalized" value={data?.finalized ?? 0} icon="badge-check" tone="ok" />
        <StatCard
          label="Average score (this page)"
          value={average === null ? '—' : `${average}%`}
          icon="trending-up"
          sub={`Compliance over decided checks · coverage ${averageCoverage === null ? '—' : `${averageCoverage}%`}`}
        />
        <StatCard
          label="Below coverage floor"
          value={data?.facets.below_coverage_floor ?? 0}
          icon="alert-triangle"
          tone={(data?.facets.below_coverage_floor ?? 0) > 0 ? 'warn' : 'ok'}
          sub={`Under ${coverageFloor}% of the rules verified from evidence`}
        />
      </div>

      <Card>
        <div className="filter-bar">
          <div className="field grow">
            <label htmlFor="repo-q">Search</label>
            <input
              id="repo-q"
              value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(1) }}
              placeholder="Product, brand, manufacturer, batch or scan number…"
            />
          </div>
          <div className="field">
            <label htmlFor="repo-verdict">AI verdict</label>
            <select id="repo-verdict" value={verdict} onChange={(e) => { setVerdict(e.target.value); setPage(1) }}>
              <option value="">All verdicts</option>
              <option value="COMPLIANT">Pass-oriented (preliminary)</option>
              <option value="NON_COMPLIANT">Non-compliant</option>
              <option value="NEEDS_MANUAL_REVIEW">Needs review</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="repo-cat">Category</label>
            <select id="repo-cat" value={category} onChange={(e) => { setCategory(e.target.value); setPage(1) }}>
              <option value="">All categories</option>
              {categories.map(([name, count]) => (
                <option key={name} value={name}>{name} ({count})</option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="repo-sort">Sort</label>
            <select id="repo-sort" value={sort} onChange={(e) => { setSort(e.target.value); setPage(1) }}>
              <option value="recent">Newest scan first</option>
              <option value="oldest">Oldest scan first</option>
              <option value="score_asc">Lowest score first</option>
              <option value="score_desc">Highest score first</option>
              <option value="name">Product name</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="repo-min">Min score</label>
            <input id="repo-min" inputMode="numeric" value={minScore} onChange={(e) => { setMinScore(e.target.value); setPage(1) }} placeholder="0" />
          </div>
          <div className="field">
            <label htmlFor="repo-max">Max score</label>
            <input id="repo-max" inputMode="numeric" value={maxScore} onChange={(e) => { setMaxScore(e.target.value); setPage(1) }} placeholder="100" />
          </div>
          <button className="btn sm secondary" onClick={resetFilters} disabled={!activeFilters}>
            Clear filters
          </button>
        </div>

        <div className="facet-row" role="group" aria-label="Filter by review status">
          <button className={`facet ${reviewStatus === '' ? 'on' : ''}`} onClick={() => { setReviewStatus(''); setPage(1) }}>
            All statuses <b>{data?.total ?? 0}</b>
          </button>
          <button
            className={`facet ${belowFloor ? 'on' : ''}`}
            onClick={() => { setBelowFloor(!belowFloor); setPage(1) }}
            title={`Scans where fewer than ${coverageFloor}% of the applicable requirements could be verified from the evidence`}
          >
            Low evidence coverage <b>{data?.facets.below_coverage_floor ?? 0}</b>
          </button>
          {statuses.map(([name, count]) => (
            <button
              key={name}
              className={`facet ${reviewStatus === name ? 'on' : ''}`}
              onClick={() => { setReviewStatus(name); setPage(1) }}
            >
              {STATUS_LABEL[name] ?? name} <b>{count}</b>
            </button>
          ))}
        </div>

        {error && <div className="alert error">{error}</div>}
        {loading ? (
          <Skeleton lines={6} />
        ) : (data?.items.length ?? 0) === 0 ? (
          <EmptyState
            glyph="◌"
            headline={activeFilters ? 'No scans match these filters' : 'No scans in the repository yet'}
            how={
              activeFilters
                ? 'Try clearing the filters, or search a different product name, batch or scan number.'
                : 'Run an inspection (New Inspection → start scan); each processed scan is stored here automatically with its score, checks and decision.'
            }
            action={
              activeFilters ? (
                <button className="btn sm secondary" onClick={resetFilters}>Clear filters</button>
              ) : (
                <Link className="btn sm" to="/inspections/new">Start an inspection</Link>
              )
            }
          />
        ) : (
          <>
            <div className="grid product-grid">
              {data!.items.map((scan) => (
                <ScanCardView
                  key={scan.id}
                  scan={scan}
                  threshold={threshold}
                  coverageFloor={coverageFloor}
                  onOpen={() => navigate(`/repository/scans/${scan.id}`)}
                />
              ))}
            </div>
            <div className="flex mt" style={{ alignItems: 'center' }}>
              <button className="btn sm secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>← Prev</button>
              <span className="muted">Page {page} of {totalPages} · {data!.total} scan(s)</span>
              <button className="btn sm secondary right" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>Next →</button>
            </div>
          </>
        )}
      </Card>
    </>
  )
}

/** Compact repository card: image, identity, score and status only — details open on click. */
export function ScanCardView({
  scan,
  onOpen,
  threshold = 85,
  coverageFloor = 70,
}: {
  scan: ScanCard
  onOpen: () => void
  threshold?: number
  coverageFloor?: number
}) {
  const imageUrl = scan.image_filename ? api.fileUrl('originals', scan.image_filename) : ''
  return (
    <button className="product-card" onClick={onOpen} aria-label={`Open ${scan.product_name}`}>
      <span className="thumb">
        {imageUrl ? <img src={imageUrl} alt="" loading="lazy" /> : <span className="ph" aria-hidden="true">◌</span>}
      </span>
      <span className="body">
        <span className="name">{scan.product_name || '(name not read)'}</span>
        <span className="cat">{scan.category || 'OTHER'}{scan.brand ? ` · ${scan.brand}` : ''}</span>
        <span className="score-line" style={{ display: 'block' }}>
          <ScoreMeterInline score={scan.compliance_score} threshold={threshold} />
        </span>
        <span className="score-line" style={{ display: 'block' }}>
          <span
            className={`badge ${scan.coverage_score >= coverageFloor ? 'PASS' : 'COVERAGE_LOW'}`}
            title={`Evidence coverage: ${scan.coverage_score}% of the applicable requirements were decided from the evidence (floor ${coverageFloor}%)`}
          >
            COVERAGE {scan.coverage_score}%
          </span>
        </span>
        <span className="status">
          <StatusChip value={scan.review_status} />
        </span>
        <span className="key">{scan.status_text}</span>
        <span className="open-hint">
          Scan {scan.inspection_number} · {scan.scanned_at.slice(0, 10)} · view details →
        </span>
      </span>
    </button>
  )
}

function ScoreMeterInline({ score, threshold = 85 }: { score: number; threshold?: number }) {
  const tone = score >= threshold ? 'pass' : score >= Math.max(0, threshold - 15) ? 'warn' : 'fail'
  return (
    <span className={`score ${tone}`}>
      <span className="num">{score}%</span>
      <span className="track" aria-hidden="true"><i style={{ width: `${Math.max(0, Math.min(100, score))}%` }} /></span>
    </span>
  )
}

export { STATUS_LABEL as REPO_STATUS_LABEL, VERDICT_LABEL as REPO_VERDICT_LABEL }
