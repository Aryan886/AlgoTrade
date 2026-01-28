import type { EngineStatus } from '../types/engine';

export async function fetchStatus(): Promise<EngineStatus>{
    const res = await fetch("/status");

    if(!res.ok){
        throw new Error("Status fetch failed");
    }

    return res.json();
}