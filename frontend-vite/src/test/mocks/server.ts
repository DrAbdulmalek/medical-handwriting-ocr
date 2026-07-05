/**
 * MSW server setup for integration tests.
 *
 * Imports all request handlers and creates a server instance.
 * Individual test files can start/stop the server via `beforeAll`/`afterAll`
 * or use the `setupFiles` approach with `setupServer` from MSW.
 */

import { setupServer } from 'msw/node';
import { handlers } from './handlers';

/**
 * Pre-configured MSW server for use in Vitest integration tests.
 *
 * Usage in test files:
 *
 *   import { server } from '@/test/mocks/server';
 *
 *   beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
 *   afterEach(() => server.resetHandlers());
 *   afterAll(() => server.close());
 */
export const server = setupServer(...handlers);
