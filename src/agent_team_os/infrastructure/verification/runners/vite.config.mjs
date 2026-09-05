// 不读取 Candidate 的插件、开发代理或构建钩子。
export default {
  root: process.env.ATOS_VERIFICATION_WORKSPACE,
  cacheDir: process.env.ATOS_VERIFICATION_CACHE,
  build: { emptyOutDir: true, sourcemap: false },
};
