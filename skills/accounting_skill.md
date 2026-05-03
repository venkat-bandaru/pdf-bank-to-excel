# Accountant Skill

## Description
The accountant skill enables Claude to process financial documents, extract relevant data, and generate structured Excel spreadsheets for accounting purposes, including VAT calculations. It encapsulates domain-specific knowledge, data validation rules, accounting-specific transformations, and VAT expertise to ensure accurate and compliant outputs.

## Capabilities
1. Financial Document Parsing:
   - Identify and extract key data points from various invoice formats and layouts
   - Handle different date formats, currencies, and number conventions
   - Recognize and categorize income, expenses, taxes, and other financial fields

2. Data Validation and Error Handling:
   - Validate extracted data for completeness, consistency, and accuracy
   - Identify and handle missing or malformed data gracefully
   - Flag and resolve common issues like misaligned columns or incorrect formats

3. Accounting-Specific Transformations:
   - Apply appropriate formulas and calculations to extracted data
   - Perform conversions and aggregations based on accounting standards
   - Generate properly formatted Excel sheets with required columns and totals

4. VAT Calculations:
   - Determine applicable VAT rates based on transaction types and jurisdictions
   - Compute VAT amounts accurately based on extracted financial data
   - Generate VAT reports and summaries in compliance with regulations

5. Contextual Prompting:
   - Identify ambiguous or incomplete financial data during processing
   - Ask clarifying questions to elicit necessary context for accurate interpretation
   - Guide users to provide missing information or resolve discrepancies

## Rules
1. Maintain data integrity and confidentiality throughout the processing pipeline
2. Validate all extracted data against expected formats, ranges, and constraints
3. Apply VAT calculations consistently based on the latest rules and rates
4. Generate outputs that adhere to accounting best practices and standards
5. Provide clear feedback and error messages when issues are encountered
6. Maintain a log of all transformations and calculations for auditing purposes
7. Ensure generated Excel files are compatible with common accounting software

## Usage
To use the accountant skill in your code:
1. Import the skill module: `import accountant_skill`
2. Call the relevant functions for parsing, validation, transformation, and VAT calculations
3. Handle errors and edge cases based on the skill's guidance
4. Integrate the skill's outputs into your core application logic

Remember, the accountant skill is designed to augment Claude's base capabilities with domain-specific knowledge and rules. It should be used in conjunction with other relevant skills (like pdf and xlsx) to build a robust, end-to-end solution for processing financial documents and generating accurate Excel outputs.