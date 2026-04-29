.PHONY: prepare test clean docs

prepare:
	python3 -m venv venv
	. venv/bin/activate
	python3 -m pip install --upgrade pip
	pip install -r requirements.txt
	echo "install ckb"
	python3 -m download

	python3 -m download_ckb_light_client
	echo "install ckb cli"
	sh prepare.sh

develop_prepare:
	python3 -m venv venv
	. venv/bin/activate
	python3 -m pip install --upgrade pip
	pip install -r requirements.txt
	echo "install ckb"
	python3 -m download

	python3 -m download_ckb_light_client
	echo "install ckb cli"
	bash develop_prepare.sh
#    test_cases/ckb2023 \

test_cases := \
    test_cases/ckb_cli \
    test_cases/contracts \
    test_cases/example \
    test_cases/framework \
    test_cases/light_client \
    test_cases/mocking \
    test_cases/node_compatible \
    test_cases/rpc \
    test_cases/soft_fork \
    test_cases/issue \
    test_cases/tx_pool_refactor \
    test_cases/feature \
    test_cases/config \
    test_cases/miner \
    test_cases/get_fee_rate_statistics \
	test_cases/ws \
	test_cases/memory \
	test_cases/ckb_command


test:
	@failed_cases=; \
    for test_case in $(test_cases); do \
        echo "Running tests for $$test_case"; \
        if ! bash test.sh "$$test_case"; then \
            echo "$$test_case" >> failed_test_cases.txt; \
        fi \
    done; \
    if [ -s failed_test_cases.txt ]; then \
        echo "Some test cases failed: $$(cat failed_test_cases.txt)"; \
        rm -f failed_test_cases.txt; \
        exit 1; \
    fi

develop_test:
	@failed_cases=; \
    for test_case in $(TestCases); do \
        echo "Running tests for $$test_case"; \
        if ! bash test.sh "$$test_case"; then \
            echo "$$test_case" >> failed_test_cases.txt; \
        fi \
    done; \
    if [ -s failed_test_cases.txt ]; then \
        echo "Some test cases failed: $$(cat failed_test_cases.txt)"; \
        rm -f failed_test_cases.txt; \
        exit 1; \
    fi


clean:
	pkill ckb
	rm -rf tmp
	rm -rf download
	rm -rf report
	rm -rf source/ckb-cli*
	rm -rf ckb-*

docs:
	python -m pytest --docs=docs/soft.md --doc-type=md test_cases/soft_fork
