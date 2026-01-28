export type EngineState = 
    | "IDLE"
    | "PREVIEWING"
    | "OPEN"
    | "CLOSING"
    | "ERROR";

export interface Position { 
    id? : string;
    [key : string] : unknown;
}

export interface EngineStatus {
    engine_state : EngineState;
    mode : string;
    position : Position | null;
    last_update_ts : string | null ;
    net_pnl : number;
    error_message? : string | null;
}

