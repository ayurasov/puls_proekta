import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import globals from 'globals'

export default [
  {
    ignores: ['dist/**', 'node_modules/**', 'backend/**', 'legacy/**', 'playwright-report/**', 'test-results/**',
      'docs/**', 'tools/**'],
  },
  js.configs.recommended,
  ...pluginVue.configs['flat/essential'],
  {
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: { ...globals.browser, ...globals.node },
    },
    rules: {
      'no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      'vue/multi-word-component-names': 'off',
      'no-useless-assignment': 'off',
    },
  },
  {
    files: ['**/__tests__/**/*.test.js', 'e2e/**/*.mjs'],
    languageOptions: { globals: { ...globals.node } },
  },
]
