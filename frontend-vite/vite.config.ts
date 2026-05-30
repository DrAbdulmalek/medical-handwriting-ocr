import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

/**
 * Vite configuration for the Medical Handwriting OCR frontend.
 *
 * - Uses the React Fast Refresh plugin for HMR during development.
 * - Proxies all /api, /health, and /docs requests to the FastAPI backend.
 * - Configures a `@/` path alias pointing at `./src/` for clean imports.
 */
export default defineConfig({
  plugins: [react()],

  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },

  server: {
    port: 3000,
    proxy: {
      // Proxy API requests to the FastAPI backend running on port 8000.
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      // Proxy health-check endpoint.
      '/health': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      // Proxy OpenAPI docs (optional, handy during development).
      '/docs': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },

  build: {
    outDir: 'dist',
    sourcemap: true,
  },
});
