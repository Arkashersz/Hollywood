import { createClient } from '@supabase/supabase-js';
const url=import.meta.env.VITE_SUPABASE_URL as string|undefined;
const publishable=import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY as string|undefined;
export const supabase = url && publishable ? createClient(url,publishable) : null;
