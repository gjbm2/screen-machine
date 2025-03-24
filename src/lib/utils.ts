
import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"
import { format } from 'date-fns';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// Format date utility function
export const formatDate = (date: Date): string => {
  return format(date, 'MMM d, yyyy h:mm a');
};

/**
 * Generate a unique ID string
 * Simple implementation of nanoid for generating unique identifiers
 */
export const nanoid = (): string => {
  // Create a random string with timestamp to ensure uniqueness
  return Math.random().toString(36).substring(2, 10) + 
         '_' + 
         Date.now().toString(36);
};
