export const formatDuration = (seconds) => {
    if (!seconds) return '0ms';
    const ms = seconds * 1000;
    if (ms < 1) return `${ms.toFixed(3)}ms`;
    return `${ms.toFixed(2)}ms`;
}

export const getStatusText = (test) => {
    if (test.failed) return '✗ FAILED';
    return '✓ PASSED';
}