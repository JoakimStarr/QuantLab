/**
 * WebSocket 客户端
 * 连接 /ws 端点，支持事件订阅与自动重连
 */
class WSClient {
  constructor() {
    this.ws = null
    this.listeners = {}
    this.reconnectTimer = null
    this.reconnectAttempts = 0
    this.maxReconnectAttempts = 5
    this.baseDelay = 3000
    this.shouldReconnect = true
    this.token = undefined
  }

  /** 建立 WebSocket 连接
   * @param {string} token - 鉴权 token（后端开启鉴权时必传）
   */
  connect(token) {
    if (token !== undefined) {
      this.token = token
    }
    if (this.ws) {
      if (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING) {
        return
      }
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host
    const query = token ? `?token=${encodeURIComponent(token)}` : ''
    const url = `${protocol}//${host}/ws${query}`

    try {
      this.ws = new WebSocket(url)
    } catch (e) {
      console.error('[WS] 连接失败:', e)
      this._scheduleReconnect()
      return
    }

    this.ws.onopen = () => {
      this.reconnectAttempts = 0
      this._emit('open', { connected: true })
    }

    this.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        const type = msg.type || msg.event || 'message'
        const payload = msg.data !== undefined ? msg.data : msg
        this._emit(type, payload)
      } catch (e) {
        // 非 JSON 消息，直接透传
        this._emit('message', event.data)
      }
    }

    this.ws.onerror = (err) => {
      this._emit('error', err)
    }

    this.ws.onclose = () => {
      this._emit('close', {})
      if (this.shouldReconnect) {
        this._scheduleReconnect()
      }
    }
  }

  /** 安排自动重连 */
  _scheduleReconnect() {
    if (this.reconnectTimer) return
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      this._emit('reconnect_failed', {})
      return
    }
    this.reconnectAttempts++
    const delay = this.baseDelay * this.reconnectAttempts
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      this.connect(this.token)
    }, delay)
  }

  /**
   * 订阅事件
   * @param {string} event - 事件名称
   * @param {Function} callback - 回调函数
   * @returns {Function} 取消订阅函数
   */
  on(event, callback) {
    if (!this.listeners[event]) {
      this.listeners[event] = []
    }
    this.listeners[event].push(callback)
    return () => {
      this.listeners[event] = (this.listeners[event] || []).filter(fn => fn !== callback)
    }
  }

  /** 触发事件 */
  _emit(event, data) {
    const cbs = this.listeners[event]
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

  /**
   * 发送消息
   * @param {string} type - 消息类型
   * @param {*} data - 消息数据
   */
  send(type, data) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type, data }))
      return true
    }
    return false
  }

  /** 断开连接，不再自动重连 */
  close() {
    this.shouldReconnect = false
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
  }
}

export default new WSClient()
