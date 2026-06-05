set -e
# git clone https://github.com/nervosnetwork/ckb-cli.git
# cd ckb-cli
# git checkout pkg/v1.7.0
# make prod
# cp target/release/ckb-cli ../source/ckb-cli
# cd ../
DEFAULT_CKB_BRANCH="develop"
DEFAULT_CKB_URL="https://github.com/nervosnetwork/ckb.git"
DEFAULT_BUILD_CKB=false

DEFAULT_CKB_CLI_BRANCH="develop"
DEFAULT_CKB_CLI_URL="https://github.com/nervosnetwork/ckb-cli.git"
DEFAULT_BUILD_CKB_CLI=false


GitCKBBranch="${GitBranch:-$DEFAULT_CKB_BRANCH}"
GitCKBUrl="${GitUrl:-$DEFAULT_CKB_URL}"
GitCKBCLIBranch="${CKBCLIGitBranch:-$DEFAULT_CKB_CLI_BRANCH}"
GitCKBCLIUrl="${CKBCLIGitUrl:-$DEFAULT_CKB_CLI_URL}"
BUILD_CKB="${BuildCKb:-$DEFAULT_BUILD_CKB}"
BUILD_CKB_CLI="${BuildCKbCLI:-$DEFAULT_BUILD_CKB_CLI}"
if [ "$BUILD_CKB" == "true" ]; then
  git clone -b $GitCKBBranch $GitCKBUrl
  cd ckb
  make prod
  cp target/prod/ckb ../download/0.206.0/ckb
  cd ../
fi
if [ "$BUILD_CKB_CLI" == "true" ]; then
  git clone -b $GitCKBCLIBranch $GitCKBCLIUrl
  cd ckb-cli
  make prod
  cp target/release/ckb-cli ../source/ckb-cli
  exit 0
fi
