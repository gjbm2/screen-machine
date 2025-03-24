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
