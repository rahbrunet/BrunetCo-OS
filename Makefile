# Unix mirror of make.py. On Windows, use `python make.py <target>`.
.PHONY: dev api web test lint gen-contracts migrate rls-proof

dev:           ; python make.py dev
api:           ; python make.py api
web:           ; python make.py web
test:          ; python make.py test
lint:          ; python make.py lint
gen-contracts: ; python make.py gen-contracts
migrate:       ; python make.py migrate
rls-proof:     ; python make.py rls-proof
