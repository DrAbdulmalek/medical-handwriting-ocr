/// <reference types="vite/client" />

/**
 * Vite environment variable type declarations.
 *
 * Any variable prefixed with `VITE_` is available at runtime via
 * `import.meta.env`. Declare them here so TypeScript recognises
 * the keys and provides autocompletion.
 */
interface ImportMetaEnv {
  readonly VITE_API_URL: string;
  readonly VITE_API_KEY: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
