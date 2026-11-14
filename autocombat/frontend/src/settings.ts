import { createContext, useContext } from 'react';

export type SettingsValues = Record<string, any>;
export type SettingField = { label: string; group: string; default: any; readonly: boolean; apply: 'live' | 'startup' | 'new_task'; choices: (number | string)[] | null; kind: string };
export type SettingsResponse = { values: SettingsValues; fields: Record<string, SettingField>; version: string; can_manage: boolean; runtime: { instance: string; web_address: string; settings_file?: string; files?: Record<string, string>; web_port_argument?: string } };
export const RuntimeSettings = createContext<SettingsValues>({});
export const useRuntimeSettings = () => useContext(RuntimeSettings);

export type Preferences = { name: string; reduced: boolean };
export function readPreferences(): Preferences {
  try {
    const saved = JSON.parse(localStorage.getItem('combat-preferences') || '{}');
    return { name: String(saved.name || localStorage.getItem('combat-name') || '星旅人').slice(0, 20),
      reduced: typeof saved.reduced === 'boolean' ? saved.reduced : localStorage.getItem('combat-reduced') === 'true' };
  } catch { return { name: localStorage.getItem('combat-name') || '星旅人', reduced: localStorage.getItem('combat-reduced') === 'true' }; }
}
export function savePreferences(prefs: Preferences) {
  localStorage.setItem('combat-preferences', JSON.stringify(prefs));
  localStorage.setItem('combat-name', prefs.name);
  localStorage.setItem('combat-reduced', String(prefs.reduced));
}
