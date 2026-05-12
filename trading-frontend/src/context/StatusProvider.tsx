/* eslint-disable react-refresh/only-export-components */

import React, { createContext, useEffect, useState } from "react";
import { fetchStatus } from "../api/status";
import  type { EngineStatus } from "../types/engine";

interface StatusContextValue {
    status : EngineStatus | null;
    loading : boolean;
    networkError : boolean;
}

export const StatusContext = createContext<StatusContextValue>({
    status : null,
    loading : true,
    networkError : false,
});

export const StatusProvider : React.FC<{children : React.ReactNode}> = ({ children }) => {
    const [status, setStatus] = useState<EngineStatus | null >(null);
    const [loading, setLoading] = useState(true);
    const [networkError, setNetworkError] = useState(false);


    useEffect(() => {
        let alive = true;

        const poll = async() =>{
            try{
                const data = await fetchStatus();
                if(!alive) return;

                setStatus(data);
                setLoading(false);
                setNetworkError(false);
            }catch{
                setNetworkError(true);
            }
        };
        
        poll();
        const id = setInterval(poll, 1500);

        return () => {
            alive = false;
            clearInterval(id);
        };
    }, []);

    return (
        <StatusContext.Provider value = {{ status, loading, networkError}}>
            {children}
        </StatusContext.Provider>
    );
};
