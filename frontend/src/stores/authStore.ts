import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface User {
  id: number
  username: string
  role: 'admin' | 'user' | 'expert' | 'guest'
}

interface AuthState {
  token: string | null
  user: User | null
  login: (username: string, password: string) => Promise<void>
  logout: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      user: null,

      login: async (username: string, password: string) => {
        const res = await fetch('/api/v1/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username, password }),
        })
        const json = await res.json()
        if (json.code !== 0) {
          throw new Error(json.message)
        }
        set({ token: json.data.token, user: json.data.user })
      },

      logout: () => {
        set({ token: null, user: null })
      },
    }),
    { name: 'sqlineage-auth' }
  )
)
