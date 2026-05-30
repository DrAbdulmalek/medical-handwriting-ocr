import React from 'react';
import ReactDOM from 'react-dom/client';
import App from '@/App';
import '@/index.css';

/**
 * Application entry point.
 *
 * Uses React 18's `createRoot` API for concurrent rendering.
 * The root `<App />` component is mounted into the `#root` DOM element
 * defined in `index.html`.
 */
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
