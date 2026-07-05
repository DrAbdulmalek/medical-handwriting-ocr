/// <reference types="vitest/config" />
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],

  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },

  test: {
    // Use jsdom for DOM-related tests.
    environment: 'jsdom',

    // Global setup file for @testing-library/jest-dom matchers.
    setupFiles: ['./src/test/setup.ts'],

    // Enable global test APIs (describe, it, expect) without explicit imports.
    globals: true,

    // Exclude E2E test directory (Playwright, not Vitest).
    exclude: ['e2e/**', 'node_modules/**'],

    // CSS modules handling — skip actual CSS import in tests.
    css: true,

    coverage: {
      provider: 'v8',
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/**/__tests__/**',
        'src/**/*.test.{ts,tsx}',
        'src/test/**',
        'src/main.tsx',
        'src/vite-env.d.ts',
      ],
      thresholds: {
        branches: 70,
        functions: 70,
        lines: 70,
      },
    },
  },
});
