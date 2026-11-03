# Agentic_Chatbot

## Document-based help answers

The help agent answers bank-service, policy, and procedure questions using
documents indexed in Chroma. Add `.pdf`, `.md`, or `.txt` files under
`data/help_documents` (the legacy recipe PDF elsewhere in `data/` is not
included automatically), then build or refresh the index from the `src`
directory:

```powershell
Set-Location src
python -m utils.embeddings.help_document_store
```

The command requires `OPENAI_API_KEY` because OpenAI embeddings are used, even
when another provider is configured for chat completions. Set
`HELP_DOCUMENTS_DIR` or `HELP_CHROMA_DIR` to override the defaults. Re-running
the command replaces the help-document collection with the current source
files. Start the app after indexing; questions classified as help topics will
be retrieved from those documents rather than sent to transaction SQL.