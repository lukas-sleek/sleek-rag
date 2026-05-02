-- Plan 13.6: drop legacy OpenAI columns now that the codebase has no
-- references to them. Pre-Module-1.5 rows lose their OpenAI ids; that's
-- intentional — those documents weren't ingested through Document AI and
-- have no document_chunks rows anyway, so they couldn't be queried.

alter table public.projects      drop column if exists openai_vector_store_id;
alter table public.chats         drop column if exists openai_thread_id;
alter table public.project_files drop column if exists openai_file_id;
