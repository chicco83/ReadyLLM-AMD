import { useState, useEffect, useRef } from 'react'

// [2026-10-22 v1.1.39] Integrato dal progetto originale (htobty/ReadyLLM, commit 48e4d9e): il hook non si riconnette piu' dopo lo
// smontaggio del componente. Prima il cleanup cancellava un timer nullo e poi chiamava close(): l'handler onclose, asincrono,
// pianificava comunque una riconnessione a componente gia' smontato, lasciando un socket orfano a ogni mount (StrictMode, HMR).
//  - il timer di retry e' in un ref, cosi' si puo' davvero cancellare
//  - flag `cancelled` e callback staccate prima di chiudere
//  - con url vuoto non si apre nessuna connessione (new WebSocket('') solleva un errore)
// Versione precedente: let retryTimer locale (null alla pulizia) + wsRef.current?.close() senza staccare gli handler.
export function useWebSocket(url) {
  const [data, setData] = useState(null)
  const [connected, setConnected] = useState(false)
  const wsRef = useRef(null)
  const retryTimerRef = useRef(null)

  useEffect(() => {
    if (!url) {
      setConnected(false)
      return
    }

    let cancelled = false

    function connect() {
      if (cancelled) return
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen = () => {
        if (cancelled) return
        setConnected(true)
      }
      ws.onmessage = (event) => {
        if (cancelled) return
        try {
          setData(JSON.parse(event.data))
        } catch (e) {}
      }
      ws.onclose = () => {
        // componente gia' smontato: niente riconnessione (altrimenti resta un socket senza proprietario)
        if (cancelled) return
        setConnected(false)
        clearTimeout(retryTimerRef.current)
        retryTimerRef.current = setTimeout(connect, 3000)
      }
      ws.onerror = () => ws.close()
    }

    connect()

    return () => {
      cancelled = true
      clearTimeout(retryTimerRef.current)
      retryTimerRef.current = null
      const ws = wsRef.current
      wsRef.current = null
      if (ws) {
        // prima si staccano le callback, poi si chiude: evita eventi in ritardo dopo lo smontaggio
        ws.onopen = null
        ws.onmessage = null
        ws.onclose = null
        ws.onerror = null
        ws.close()
      }
    }
  }, [url])

  return { data, connected }
}
