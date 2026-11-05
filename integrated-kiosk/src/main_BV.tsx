// ============================================================================
// File:         main_BV.tsx
// Written by:   BV (Blas Vargas)
// What it does: Starts the React app.
// Team-written: Logic written by BV (Blas Vargas). Outside code is used only
//               through the third-party sources listed below.
// Third-party:  React; React DOM
// ============================================================================
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App_BV_CG'
import './index_BV.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
