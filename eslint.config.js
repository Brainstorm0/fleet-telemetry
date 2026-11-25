const globals = require("globals");

module.exports = [
  {
    files: ["dashboard/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: { ...globals.browser, L: "readonly", Chart: "readonly" },
    },
    rules: {
      "no-unused-vars": "warn",
      "no-undef": "error",
      eqeqeq: ["error", "smart"],
      "no-implied-eval": "error",
      "no-eval": "error",
    },
  },
];
