// 工具由产品锁定；Candidate 无权提供运行配置或 Reporter。
export default {
  root: process.env.ATOS_VERIFICATION_WORKSPACE,
  cacheDir: process.env.ATOS_VERIFICATION_CACHE,
  test: {
    include: ['tests/**/*.test.ts'],
    environment: 'node',
    passWithNoTests: false,
    pool: 'forks',
    poolOptions: { forks: { singleFork: true } },
  },
};
