import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/** Where the API actually lives. Override with POCKET_API_TARGET when the backend moves. */
const API_TARGET = process.env.POCKET_API_TARGET || 'http://127.0.0.1:8001'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
        configure: (proxy) => {
          // When the backend is down (or still starting) Vite answers with an empty 500 and the
          // user only sees "Request failed (500)". Reply with an actionable JSON body instead so
          // the UI can explain exactly what is wrong.
          proxy.on('error', (err: Error & { code?: string }, _req, res) => {
            const target = res as unknown as {
              writeHead?: (code: number, headers: Record<string, string>) => void
              headersSent?: boolean
              end?: (body?: string) => void
              destroy?: () => void
            }
            const detail =
              `Cannot reach the POCKET backend at ${API_TARGET} (${err.code || err.message}). ` +
              'Start it with start.bat, or run: .venv\\Scripts\\python -m uvicorn backend.main:app --port 8001'
            const body = JSON.stringify({ detail })
            if (typeof target.writeHead === 'function' && !target.headersSent) {
              target.writeHead(503, { 'Content-Type': 'application/json' })
              target.end?.(body)
            } else {
              target.destroy?.()
            }
          })
        },
      },
    },
  },
})
