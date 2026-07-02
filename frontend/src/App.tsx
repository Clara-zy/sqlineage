import { BrowserRouter, Routes, Route } from 'react-router-dom'
import LoginPage from './pages/LoginPage'
import WorkbenchPage from './pages/WorkbenchPage'
import TableListPage from './pages/TableListPage'
import TableDetailPage from './pages/TableDetailPage'
import NodeSearchPage from './pages/NodeSearchPage'
import NodeDetailPage from './pages/NodeDetailPage'
import AdminUsersPage from './pages/AdminUsersPage'
import AdminStatsPage from './pages/AdminStatsPage'
import Navbar from './components/Navbar'
import { useAuthStore } from './stores/authStore'

function App() {
  const token = useAuthStore((s) => s.token)

  if (!token) {
    return <LoginPage />
  }

  return (
    <BrowserRouter>
      <div className="min-h-screen bg-gray-50 flex flex-col">
        <Navbar />
        <main className="flex-1">
          <Routes>
            <Route path="/" element={<WorkbenchPage />} />
            <Route path="/tables" element={<TableListPage />} />
            <Route path="/tables/:id" element={<TableDetailPage />} />
            <Route path="/nodes" element={<NodeSearchPage />} />
            <Route path="/nodes/:hash" element={<NodeDetailPage />} />
            <Route path="/admin/users" element={<AdminUsersPage />} />
            <Route path="/admin/stats" element={<AdminStatsPage />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}

export default App
