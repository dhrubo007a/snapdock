import { useEffect, useState } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import axios from 'axios'
import Layout from './components/Layout'
import DashboardPage from './pages/DashboardPage'
import SnapshotHistoryPage from './pages/SnapshotHistoryPage'
import SnapshotInspectorPage from './pages/SnapshotInspectorPage'
import AllSnapshotsPage from './pages/AllSnapshotsPage'
import CoveragePage from './pages/CoveragePage'
import LoginPage from './pages/LoginPage'
import SetupPage from './pages/SetupPage'
import SettingsPage from './pages/SettingsPage'
import AuditPage from './pages/AuditPage'

/** Polls /api/setup/status once on mount. Returns null while loading. */
function useSetupRequired(): boolean | null {
  const [required, setRequired] = useState<boolean | null>(null)
  useEffect(() => {
    axios.get('/api/setup/status')
      .then((r) => setRequired(r.data.required as boolean))
      .catch(() => setRequired(false)) // If unreachable assume normal boot
  }, [])
  return required
}

function ProtectedLayout() {
  const token   = localStorage.getItem('snapdock_token')
  const setupReq = useSetupRequired()

  // Still loading setup status — render nothing to avoid flash
  if (setupReq === null) return null

  if (setupReq) return <Navigate to="/setup" replace />
  if (!token)   return <Navigate to="/login" replace />
  return <Layout />
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/setup" element={<SetupPage />} />
      <Route element={<ProtectedLayout />}>
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/snapshots" element={<AllSnapshotsPage />} />
        <Route path="/stacks/:stackName/snapshots" element={<SnapshotHistoryPage />} />
        <Route path="/stacks/:stackName/snapshots/:snapshotId" element={<SnapshotInspectorPage />} />
        <Route path="/coverage" element={<CoveragePage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/audit" element={<AuditPage />} />
      </Route>
    </Routes>
  )
}
