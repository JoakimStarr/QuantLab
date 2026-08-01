/**
 * WebSocket composable
 * 支持自动重连、心跳、事件订阅
 */
import { ref, onUnmounted } from 'vue'

export function useWebSocket(url, options = {}) {
  const {
    reconnectInterval = 3000,
    maxReconnectAttempts = 10,
    heartbeatInterval = 30000,
    onMessage = () => {},
    onOpen = () => {},
    onClose = () => {},
    onError = () => {},
  } = options

  const ws = ref(null)
  const isConnected = ref(false)
  const reconnectAttempts = ref(0)

  let heartbeatTimer = null
  let reconnectTimer = null
  let shouldReconnect = true
  let currentToken = undefined
  const listeners = {}

  /** 解析 WebSocket URL */
  function resolveUrl() {
    if (url) return url
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host
    const query = currentToken ? `?token=${encodeURIComponent(currentToken)}` : ''
    return `${protocol}//${host}/ws${query}`
  }

  /** 建立连接 */
  function connect(token) {
    if (token !== undefined) {
      currentToken = token
    }
    if (ws.value && ws.value.readyState === WebSocket.OPEN) return

    shouldReconnect = true
    const wsUrl = resolveUrl()

    try {
      ws.value = new WebSocket(wsUrl)
    } catch (e) {
      console.error('[WS] 连接失败:', e)
      attemptReconnect()
      return
    }

    ws.value.onopen = (event) => {
      isConnected.value = true
      reconnectAttempts.value = 0
      emit('open', { connected: true })
      onOpen(event)
      startHeartbeat()
    }

    ws.value.onmessage = (event) => {
      onMessage(event)
      // 解析 JSON 消息，按类型派发
      try {
        const msg = JSON.parse(event.data)
        const type = msg.type || msg.event || 'message'
        const payload = msg.data !== undefined ? msg.data : msg
        emit(type, payload)
      } catch (e) {
        // 非 JSON 消息，直接透传
        emit('message', event.data)
      }
    }

    ws.value.onclose = (event) => {
      isConnected.value = false
      stopHeartbeat()
      emit('close', {})
      onClose(event)
      if (shouldReconnect) {
        attemptReconnect()
      }
    }

    ws.value.onerror = (event) => {
      onError(event)
    }
  }

  /** 断开连接，不再自动重连 */
  function disconnect() {
    shouldReconnect = false
    stopHeartbeat()
    clearTimeout(reconnectTimer)
    if (ws.value) {
      ws.value.close()
      ws.value = null
    }
    isConnected.value = false
  }

  /** 发送原始数据 */
  function send(data) {
    if (ws.value && ws.value.readyState === WebSocket.OPEN) {
      ws.value.send(data)
      return true
    }
    return false
  }

  /** 发送结构化消息 */
  function sendJSON(type, data) {
    return send(JSON.stringify({ type, data }))
  }

  /** 开始心跳 */
  function startHeartbeat() {
    heartbeatTimer = setInterval(() => {
      if (ws.value && ws.value.readyState === WebSocket.OPEN) {
        ws.value.send(JSON.stringify({ type: 'ping' }))
      }
    }, heartbeatInterval)
  }

  /** 停止心跳 */
  function stopHeartbeat() {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer)
      heartbeatTimer = null
    }
  }

  /** 自动重连 */
  function attemptReconnect() {
    if (reconnectAttempts.value >= maxReconnectAttempts) {
      emit('reconnect_failed', {})
      return
    }
    reconnectAttempts.value++
    reconnectTimer = setTimeout(connect, reconnectInterval)
  }

  /** 订阅事件 */
  function on(event, callback) {
    if (!listeners[event]) {
      listeners[event] = []
    }
    listeners[event].push(callback)
    return () => {
      listeners[event] = (listeners[event] || []).filter(fn => fn !== callback)
    }
  }

  /** 触发事件 */
  function emit(event, data) {
    const cbs = listeners[event]
    if (cbs) {
      cbs.forEach(cb => {
        try {
          cb(data)
        } catch (e) {
          console.error('[WS] 监听器执行出错:', e)
        }
      })
    }
  }

  onUnmounted(() => {
    disconnect()
  })

  return {
    ws,
    isConnected,
    connect,
    disconnect,
    send,
    sendJSON,
    on,
  }
}