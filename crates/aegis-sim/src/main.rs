use aegis_sim::{validate_dataset, Validation};
use std::{env, fs, process::ExitCode};

fn main() -> ExitCode {
    let args: Vec<_> = env::args_os().collect();
    if args.len() != 3 {
        eprintln!("Usage: aegis-sim <manifest.json> <data.json>");
        return ExitCode::from(1);
    }
    let (manifest, data) = match (fs::read(&args[1]), fs::read(&args[2])) {
        (Ok(manifest), Ok(data)) => (manifest, data),
        (Err(error), _) => {
            eprintln!("aegis-sim: {}: {error}", args[1].to_string_lossy());
            println!("FAILED: could not read input files");
            return ExitCode::from(1);
        }
        (_, Err(error)) => {
            eprintln!("aegis-sim: {}: {error}", args[2].to_string_lossy());
            println!("FAILED: could not read input files");
            return ExitCode::from(1);
        }
    };
    match validate_dataset(&manifest, &data) {
        Validation::Ready => {
            println!("READY: structurally valid input; source completeness is not established");
            ExitCode::SUCCESS
        }
        Validation::Hold(reason) => {
            println!("HOLD: {reason}");
            ExitCode::from(2)
        }
        Validation::Failed(reason) => {
            println!("FAILED: {reason}");
            ExitCode::from(1)
        }
    }
}
