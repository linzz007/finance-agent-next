ALTER TABLE h2_budgets ADD COLUMN self_call_limit INTEGER
  CHECK (self_call_limit IS NULL OR self_call_limit > 0);
