@echo off
REM Create main project folder
mkdir workday-cr-test-gen

REM Create top-level files
type nul > workday-cr-test-gen\.env
type nul > workday-cr-test-gen\requirements.txt
type nul > workday-cr-test-gen\shared.py
type nul > workday-cr-test-gen\ingest.py
type nul > workday-cr-test-gen\pipeline.py

REM Create agents folder and files
mkdir workday-cr-test-gen\agents
type nul > workday-cr-test-gen\agents\__init__.py
type nul > workday-cr-test-gen\agents\retriever.py
type nul > workday-cr-test-gen\agents\analyzer.py
type nul > workday-cr-test-gen\agents\generator.py

REM Create data/solutions folder
mkdir workday-cr-test-gen\data
mkdir workday-cr-test-gen\data\solutions

REM Create generated_tests folder
mkdir workday-cr-test-gen\generated_tests

REM Move into project directory
cd workday-cr-test-gen

echo Project structure created successfully!