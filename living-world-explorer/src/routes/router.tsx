import { createBrowserRouter } from 'react-router-dom'
import { AppShell } from '../layout/AppShell'
import { LoginPage } from '../components/LoginPage'
import { RequireAuth } from './RequireAuth'

export const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  { path: '/', element: <RequireAuth><AppShell /></RequireAuth> },
  // The dashboard owns its internal navigation. Keeping the shell mounted
  // across these routes preserves live actor selection and refresh state.
  { path: '*', element: <RequireAuth><AppShell /></RequireAuth> },
])
