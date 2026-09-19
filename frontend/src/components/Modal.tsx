// Accessible modal: Escape closes, backdrop click closes, focus moves into the
// dialog and returns to the trigger's document body on close.
import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'

export default function Modal({
  title,
  onClose,
  children,
}: {
  title: string
  onClose: () => void
  children: ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    // move focus into the dialog
    const focusable = ref.current?.querySelector<HTMLElement>(
      'input, textarea, select, button',
    )
    focusable?.focus()
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      className="modal-overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        ref={ref}
      >
        {children}
      </div>
    </div>
  )
}
