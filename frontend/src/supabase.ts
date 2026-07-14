import { createClient } from "@supabase/supabase-js";
import type { SupabaseClient } from "@supabase/supabase-js";

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL ?? "";
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY ?? "";

// Queue/detail/analytics must stay usable even when Supabase isn't
// configured (createClient throws synchronously on an empty URL, which
// would otherwise crash the whole app, not just the reviewer sign-in
// feature it's actually needed for) -- so `supabase` is null until both
// env vars are set, and callers (auth.tsx) treat that as "always signed out".
export const supabase: SupabaseClient | null =
  supabaseUrl && supabaseAnonKey ? createClient(supabaseUrl, supabaseAnonKey) : null;

if (!supabase) {
  console.warn(
    "VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY not set -- reviewer sign-in is disabled, everything else still works."
  );
}
