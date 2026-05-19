import { useEffect, useRef, useState } from 'react'
import type { SnapDockEvent } from '../types'

export function useEventStream(stackName?: string): SnapDockEvent[] {
  const [events, setEvents] = useState<SnapDockEvent[]>([])
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host
    const url = stackName
      ? `${proto}//${host}/events?stack_name=${encodeURIComponent(stackName)}`
      : `${proto}//${host}/events`

    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onmessage = (e) => {
      try {
        const event: SnapDockEvent = JSON.parse(e.data)
        if (event.event_type === 'ping') return
        setEvents((prev) => [event, ...prev].slice(0, 100))
      } catch {
        // ignore malformed frames
      }
    }

    return () => {
      ws.close()
    }
  }, [stackName])

  return events
}
