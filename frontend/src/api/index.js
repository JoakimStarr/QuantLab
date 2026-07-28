import axios from 'axios'
import { ElMessage } from 'element-plus'

function generateId() {
  const s = 'xxxxxxxxxxxx'
  return s.replace(/x/g, () => Math.floor(Math.random() * 16).toString(16))
}

const request = axios.create({
  baseURL: '/api/v1',
  timeout: 30000
})

request.interceptors.request.use(config => {
  config.headers['X-Request-ID'] = generateId()
  return config
})

request.interceptors.response.use(
  response => {
    const data = response.data
    if (!data.ok) {
      ElMessage.warning(data.error?.message || '请求失败')
      return Promise.reject(data.error)
    }
    return data.data
  },
  error => {
    if (error.response) {
      const { status, data } = error.response
      if (status === 503) {
        ElMessage.error(data?.error?.message || '服务暂时不可用')
        return Promise.reject(data?.error || error)
      }
      if (status >= 500) {
        ElMessage.error('服务器错误')
      }
    }
    return Promise.reject(error.response?.data?.error || error)
  }
)

export default request
