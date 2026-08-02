import js from '@eslint/js'
import vue from 'eslint-plugin-vue'

export default [
  js.configs.recommended,
  ...vue.configs['flat/recommended'],
  {
    rules: {
      'vue/multi-word-component-names': 'off',
      'no-unused-vars': ['warn', { argsIgnorePattern: '^_', caughtErrors: 'none' }],
      'no-empty': ['warn', { allowEmptyCatch: true }],
      // 模板格式化风格交由 prettier 统一处理
      'vue/max-attributes-per-line': 'off',
      'vue/singleline-html-element-content-newline': 'off',
      'vue/multiline-html-element-content-newline': 'off',
      'vue/html-indent': 'off',
      'vue/attributes-order': 'off',
      'vue/html-self-closing': 'off',
      'vue/first-attribute-linebreak': 'off',
    },
  },
  {
    files: ['vite.config.js', 'eslint.config.js', 'vue_parse_check.mjs'],
    languageOptions: {
      globals: {
        process: 'readonly',
        __dirname: 'readonly',
        Buffer: 'readonly',
      },
    },
  },
  {
    ignores: ['dist/**', 'node_modules/**'],
  },
]
