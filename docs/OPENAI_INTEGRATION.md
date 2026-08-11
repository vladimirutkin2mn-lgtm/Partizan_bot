# OpenAI integration note

Partizan Bot uses the OpenAI Responses API only behind the `LLMProvider` abstraction. Product intake requests structured Pydantic output, so downstream application logic receives a validated `ProductAnalysis` instead of parsing free-form JSON.

The integration is optional in local development. With `LLM_PROVIDER=mock`, the application uses a deterministic fallback and does not require an API key.
