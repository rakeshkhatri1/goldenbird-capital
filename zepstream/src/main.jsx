import React from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import ZepstreamPrototype from './ZepstreamPrototype.jsx'

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ZepstreamPrototype />
  </React.StrictMode>,
)
