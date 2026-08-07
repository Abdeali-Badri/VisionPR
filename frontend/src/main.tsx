import '@fontsource/silkscreen/400.css'
import '@fontsource/silkscreen/700.css'
import '@fontsource/space-mono/400.css'
import '@fontsource/space-mono/700.css'
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from './router'
import { App } from './App'
import './styles.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
)
