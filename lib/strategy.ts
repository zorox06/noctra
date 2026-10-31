import type { Config } from './types';

export function validateStrategy(config: Config): void {
  if (config.strategy !== 'python') throw new Error('Paste your own Python strategy. Built-in strategies have been removed.');
  if (!config.code?.trim()) throw new Error('Paste Python code defining strategy(data, params) first. Download AGENTS.md for the complete contract.');
  if (config.code.length > 100000) throw new Error('Python source must be at most 100,000 characters.');
}
