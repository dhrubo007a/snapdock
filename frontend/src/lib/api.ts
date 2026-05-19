import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
})

// Inject JWT bearer token from localStorage
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('snapdock_token')
  if (token) {
    config.headers['Authorization'] = `Bearer ${token}`
  }
  return config
})

// On 401, clear token and redirect to login
api.interceptors.response.use(
  (res) => res,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('snapdock_token')
      window.location.href = '/login'
    }
    return Promise.reject(error)
  },
)

export default api
