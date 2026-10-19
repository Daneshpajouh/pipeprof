# Why profiling saves cluster time and money

This page is for readers who do not run cluster jobs themselves.

A shared cluster such as those run by the Digital Research Alliance of Canada
works like a booking system. Before a job starts, it has to book a number of
processor cores, an amount of memory and a length of time. The scheduler
holds those resources for the job whether the job uses them or not, and the
lab's usage is counted from what was booked.

Two things go wrong when the booking is a guess:

1. Booking too little. If a job needs 10 GB of memory and booked 8, the
   system stops it the moment it crosses 8 GB. If it needs 5 hours and booked
   4, it is stopped at 4 hours. The work done so far is usually lost, and the
   job goes back to the end of the queue.
2. Booking too much. A job that booked 64 GB and used 6 GB kept 58 GB
   away from everyone else. Bigger bookings also wait longer to start, because
   the scheduler has to find a gap big enough. Over hundreds of samples, the
   waste adds up to days of computer time.

`hjp` replaces the guess with a short test. It runs the job on small slices of
the real data (1 to 20 percent), measures how time and memory grow as the
slice grows, and predicts what the full job will need, with a margin. After
the job, it compares what was booked with what was used and suggests a better
booking for next time.

A real example from this repository: sorting the public ClinVar variant file
(4.6 million records) took 23.6 seconds and 465 MB of memory on a desktop
machine. The first prediction overestimated memory because the sorting tool
stops growing its memory at a fixed limit, which the small slices never
reached. Re-running the test with bigger slices fixed most of that. Neither
booking was too small, so the job would not have been killed. The details,
including the cases where the prediction was low, are in
[results.md](results.md).
