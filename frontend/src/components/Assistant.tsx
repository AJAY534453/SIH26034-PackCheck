// PRO — the in-application assistant.
//
// PRO is not a generic chat window: every answer is assembled by the backend from this
// application's own knowledge base, its versioned rule data and the caller's own records, and each
// one carries its sources, a role-aware sign-off and — when a record is open — an answer about THAT
// record. The record id is sent as a hint only; the server re-resolves it inside the caller's scope.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api } from '../services/api'
import type { AssistantContext, AssistantReply, AssistantWelcome } from '../types'
import { Icon } from './Icon'

interface Turn {
  role: 'me' | 'bot'
  text: string
  reply?: AssistantReply
}

const KIND_LABEL: Record<string, string> = {
  app_guidance: 'Application guidance',
  workflow: 'How to do it',
  legal_reference: 'Legal reference',
  glossary: 'Meaning',
}

export function Assistant() {
  const [open, setOpen] = useState(false)
  const [welcome, setWelcome] = useState<AssistantWelcome | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const bodyRef = useRef<HTMLDivElement | null>(null)
  const inputRef = useRef<HTMLInputElement | null>(null)
  const loadedKey = useRef<string>('')
  const navigate = useNavigate()
  const location = useLocation()

  // The record the user is looking at, derived from the route. A hint only.
  const context = useMemo<AssistantContext>(() => {
    const inspection = /^\/inspections\/(\d+)/.exec(location.pathname)
    const scan = /^\/repository\/scans\/(\d+)/.exec(location.pathname)
    return {
      path: location.pathname,
      inspection_id: inspection ? Number(inspection[1]) : null,
      scan_id: scan ? Number(scan[1]) : null,
    }
  }, [location.pathname])
  const contextKey = `${context.inspection_id ?? ''}:${context.scan_id ?? ''}`

  // Opening state (greeting, role/record-aware suggestions) is loaded on first open and re-loaded
  // when the open record changes, so PRO always greets about what is actually on screen.
  useEffect(() => {
    if (!open) return
    if (loadedKey.current === contextKey && welcome) return
    loadedKey.current = contextKey
    api
      .assistantWelcome(context)
      .then((w) => {
        setWelcome(w)
        setTurns([
          {
            role: 'bot',
            text: [w.greeting, w.context_line, w.note].filter(Boolean).join(' '),
          },
        ])
        setError('')
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'PRO is unavailable'))
  }, [open, welcome, context, contextKey])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight
  }, [turns, busy])

  const send = useCallback(
    async (question: string) => {
      const text = question.trim()
      if (!text || busy) return
      setInput('')
      setError('')
      setTurns((prev) => [...prev, { role: 'me', text }])
      setBusy(true)
      try {
        const reply = await api.assistantAsk(text, context)
        setTurns((prev) => [...prev, { role: 'bot', text: reply.answer, reply }])
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Could not reach PRO')
      } finally {
        setBusy(false)
        inputRef.current?.focus()
      }
    },
    [busy, context],
  )

  return (
    <>
      {!open && (
        <button className="chat-fab" onClick={() => setOpen(true)} aria-label="Open PRO, the application assistant">
          <Icon name="message-circle" size={16} />
          Ask PRO
        </button>
      )}
      {open && (
        <div className="chat-panel" role="dialog" aria-label="PRO — application assistant" aria-modal="false">
          <div className="chat-head">
            <Icon name="sparkles" size={16} />
            <div>
              <div className="t">PRO{welcome?.name && welcome.name !== 'PRO' ? ` · ${welcome.name}` : ''}</div>
              <div className="s">
                {welcome?.context_line
                  ? 'Answers about this record, the application and its legal references'
                  : 'Application assistant — how-to help and legal references, not a legal decision'}
              </div>
            </div>
            <button className="x" onClick={() => setOpen(false)} aria-label="Close PRO">
              <Icon name="x" size={16} />
            </button>
          </div>

          <div className="chat-body" ref={bodyRef}>
            {turns.map((turn, i) => (
              <div key={i} className={`chat-msg ${turn.role === 'me' ? 'me' : 'bot'}`}>
                {turn.reply?.kind && <div className="chat-kind">{KIND_LABEL[turn.reply.kind] ?? turn.reply.kind}</div>}
                <div>{turn.text}</div>
                {turn.reply?.points && turn.reply.points.length > 0 && (
                  <ul>
                    {turn.reply.points.map((p, j) => (
                      <li key={j}>{p}</li>
                    ))}
                  </ul>
                )}
                {turn.reply?.sources && turn.reply.sources.length > 0 && (
                  <div className="chat-src">
                    Source: {turn.reply.sources.map((s) => s.label).join(' · ')}
                  </div>
                )}
                {turn.reply?.link && (
                  <div style={{ marginTop: 8 }}>
                    <button
                      className="btn sm secondary"
                      onClick={() => {
                        navigate(turn.reply!.link)
                        setOpen(false)
                      }}
                    >
                      Open that page
                    </button>
                  </div>
                )}
                {turn.reply?.closing && <div className="chat-closing">{turn.reply.closing}</div>}
              </div>
            ))}
            {busy && <div className="chat-msg bot" role="status">Thinking…</div>}
            {error && <div className="alert error">{error}</div>}
            {welcome && turns.length <= 2 && (
              <div className="chat-suggest">
                {welcome.suggestions.map((s) => (
                  <button key={s} onClick={() => send(s)}>
                    {s}
                  </button>
                ))}
              </div>
            )}
          </div>

          <form
            className="chat-input"
            onSubmit={(e) => {
              e.preventDefault()
              send(input)
            }}
          >
            <label htmlFor="assistant-input" className="sr-only" style={{ position: 'absolute', left: -9999 }}>
              Ask PRO a question
            </label>
            <input
              id="assistant-input"
              ref={inputRef}
              value={input}
              maxLength={500}
              onChange={(e) => setInput(e.target.value)}
              placeholder={welcome?.context_line ? 'e.g. Why does this need finalization?' : 'e.g. How do I finalize a pending decision?'}
            />
            <button className="btn" type="submit" disabled={busy || !input.trim()}>
              Send
            </button>
          </form>
        </div>
      )}
    </>
  )
}
